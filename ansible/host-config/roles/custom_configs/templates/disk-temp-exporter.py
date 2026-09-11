#!/usr/bin/env python3
# Disk temperature exporter for the Raspberry Pi k3s nodes.
#
# Problem: the Pis boot from USB-attached SATA SSDs. USB-SAT drive temperature
# is NOT surfaced through the kernel hwmon interface, so node-exporter's built-in
# collectors never see it. (CPU/SoC temp comes for free via node_thermal_zone_temp.)
# The June 2026 thermal outage — SSDs running 72–93°C — went completely unnoticed
# because nothing exported these temps.
#
# Solution: this script runs every 60s via systemd timer, reads SMART temps for
# every drive smartctl can see, and writes them to the node-exporter textfile
# collector directory. The in-cluster kube-prometheus-stack node-exporter DaemonSet
# mounts that directory (read-only) and exposes the metrics on its existing scrape,
# so no extra Prometheus target is needed.
#
# Also exports drive health/wear from the SAME smartctl call. Added 2026-09-11 after
# k3's SSD reached 0% available reserved space ("Drive failure expected in less than
# 24 hours") with nobody noticing -- this script had been reading the attribute table
# every 60s for months and discarding everything except attribute 194.
#
# Note the wear metrics are NOT a write-volume story: k5 has written 80.7 TiB over 848
# average P/E cycles and sits at 100% reserve, while k3 failed at 57 P/E cycles in 57
# days. Reserve exhaustion on a lightly-used drive means defects, not wear.
#
# Metrics (hdd_temperature_celsius matches the prox0 convention in hdd-fan-curve.py):
#   hdd_temperature_celsius{drive="/dev/sda",model="...",node="<hostname>"} <temp>
#   smart_health_passed{...}                     1 = SMART self-assessment PASSED
#   smart_available_reserved_space_percent{...}  spare blocks left; <5 = drive declares failure
#   smart_reallocated_sector_count{...}          blocks retired to spares
#   smart_erase_fail_count{...}                  controller could not erase a block (defect signal)
#   smart_power_on_hours{...}
#   smart_total_writes_gib{...}                  where the drive reports it

import os
import re
import socket
import subprocess

TEXTFILE_DIR  = "/var/lib/node_exporter/textfile_collector"
TEXTFILE_PATH = os.path.join(TEXTFILE_DIR, "disk-temp-exporter.prom")
NODE          = socket.gethostname()


def smartctl(args):
    return subprocess.check_output(["smartctl"] + args, text=True,
                                   stderr=subprocess.DEVNULL)


def scan_devices():
    """Return [(device, smartctl_type), ...] from `smartctl --scan`.

    Lines look like: `/dev/sda -d sat # /dev/sda [SAT], ATA device`
    """
    devices = []
    try:
        out = smartctl(["--scan"])
    except Exception:
        return devices
    for line in out.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        dev = parts[0]
        dtype = parts[2] if len(parts) >= 3 and parts[1] == "-d" else "auto"
        devices.append((dev, dtype))
    return devices


# Vendor names differ for the same concept; map them onto stable metric names.
ATTR_MAP = {
    "Perc_Avail_Resrvd_Space":  ("smart_available_reserved_space_percent", "value"),
    "Available_Reservd_Space":  ("smart_available_reserved_space_percent", "value"),
    "Reallocated_Sector_Ct":    ("smart_reallocated_sector_count", "raw"),
    "Erase_Fail_Count":         ("smart_erase_fail_count", "raw"),
    "Program_Fail_Count":       ("smart_program_fail_count", "raw"),
    "Power_On_Hours":           ("smart_power_on_hours", "raw"),
    "Total_Writes_GiB":         ("smart_total_writes_gib", "raw"),
    "Unexpect_Power_Loss_Ct":   ("smart_unexpected_power_loss_count", "raw"),
    "Wear_Leveling_Count":      ("smart_wear_leveling_count", "raw"),
    "Media_Wearout_Indicator":  ("smart_media_wearout_indicator", "value"),
}


def read_health_and_attrs(dev, dtype):
    """Return (health_passed|None, {metric_name: number}) from one `smartctl -H -A`.

    Only the -d type that `smartctl --scan` reported is ever used. Do NOT probe
    speculative -d types: on 2026-09-06 iterating through usbjmicron/usbsunplus/etc
    hung the USB bridges on k1 and k2 and took both nodes offline.
    """
    # smartctl exits NON-ZERO on a failing drive (bit 3 = "DISK FAILING", bit 4 =
    # prefail attribute at/below threshold). check_output would raise there, so this
    # metric would go missing precisely on the drives it exists to catch -- which is
    # what happened on k3 in testing. Read stdout regardless of exit status; only a
    # parse failure or missing binary counts as an error.
    try:
        proc = subprocess.run(["smartctl", "-H", "-A", "-d", dtype, dev],
                              capture_output=True, text=True)
        out = proc.stdout
    except Exception:
        return None, {}
    if not out:
        return None, {}
    health, attrs = None, {}
    for line in out.splitlines():
        low = line.lower()
        if "overall-health" in low:
            health = 1 if "passed" in low else 0
            continue
        f = line.split()
        # attribute rows: ID# NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW
        if len(f) >= 10 and f[0].isdigit() and f[1] in ATTR_MAP:
            name, which = ATTR_MAP[f[1]]
            token = f[3] if which == "value" else f[9]
            try:
                attrs[name] = int(str(token).split()[0])
            except (ValueError, IndexError):
                pass
    return health, attrs


def read_temp(dev, dtype):
    """Return integer °C for a device, or None if no temperature is reported."""
    try:
        out = smartctl(["-A", "-d", dtype, dev])
    except Exception:
        return None
    for line in out.splitlines():
        # ATA SMART attribute table: id 194 Temperature_Celsius, raw value at col 9.
        if re.match(r'\s*194\s', line):
            try:
                return int(line.split()[9])
            except (IndexError, ValueError):
                pass
        # SCSI/NVMe style: "Current Drive Temperature: 34 C" / "Temperature: 34 Celsius"
        m = re.search(r'(?:Current Drive Temperature|Temperature):\s+(\d+)', line)
        if m:
            return int(m.group(1))
    return None


def read_model(dev, dtype):
    try:
        out = smartctl(["-i", "-d", dtype, dev])
    except Exception:
        return ""
    for line in out.splitlines():
        if line.startswith(("Device Model:", "Model Number:", "Product:")):
            return line.split(":", 1)[1].strip().replace(" ", "_")
    return ""


def main():
    lines = [
        "# HELP hdd_temperature_celsius Drive temperature from SMART (USB-SAT SSDs not visible via hwmon)",
        "# TYPE hdd_temperature_celsius gauge",
    ]
    health_lines, attr_lines = [], []
    for dev, dtype in scan_devices():
        model = read_model(dev, dtype)
        labels = f'drive="{dev}",model="{model}",node="{NODE}"'

        temp = read_temp(dev, dtype)
        if temp is not None:  # empty slots / no temp sensor still get health+wear below
            lines.append(f'hdd_temperature_celsius{{{labels}}} {temp}')

        health, attrs = read_health_and_attrs(dev, dtype)
        if health is not None:
            health_lines.append(f'smart_health_passed{{{labels}}} {health}')
        for name, val in sorted(attrs.items()):
            attr_lines.append(f'{name}{{{labels}}} {val}')
    lines.append("")

    if health_lines:
        lines += [
            "# HELP smart_health_passed SMART overall-health self-assessment (1 = PASSED, 0 = FAILED)",
            "# TYPE smart_health_passed gauge",
        ] + health_lines
    if attr_lines:
        lines += [
            "# HELP smart_available_reserved_space_percent Spare blocks remaining; drives declare failure below ~5",
            "# TYPE smart_available_reserved_space_percent gauge",
        ] + attr_lines
    lines.append("")

    os.makedirs(TEXTFILE_DIR, exist_ok=True)
    tmp = TEXTFILE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, TEXTFILE_PATH)


if __name__ == "__main__":
    main()
