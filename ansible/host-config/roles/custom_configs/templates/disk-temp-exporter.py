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
# Metric (matches the prox0 convention in hdd-fan-curve.py for dashboard reuse):
#   hdd_temperature_celsius{drive="/dev/sda",model="...",node="<hostname>"} <temp>

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
    for dev, dtype in scan_devices():
        temp = read_temp(dev, dtype)
        if temp is None:
            continue  # empty card-reader slots / drives without a temp sensor
        model = read_model(dev, dtype)
        lines.append(
            f'hdd_temperature_celsius{{drive="{dev}",model="{model}",node="{NODE}"}} {temp}'
        )
    lines.append("")

    os.makedirs(TEXTFILE_DIR, exist_ok=True)
    tmp = TEXTFILE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, TEXTFILE_PATH)


if __name__ == "__main__":
    main()
