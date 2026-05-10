#!/usr/bin/env python3
# HDD temperature fan curve for prox0 — step algorithm (active)
#
# Problem: HDDs are passed through to TrueNAS VM, so the BMC has no visibility
# into drive temps and can't drive SYS_FAN3/4/5 based on them.
#
# Solution: This script runs every 60s via systemd timer, reads SMART temps
# from /dev/sda-d, and injects a fixed-duty policy into the BMC fan profile
# via the Gigabyte OEM Redfish endpoint (FanprofileService/Fanprofile). The
# BMC takes the MAX duty across all policies per fan, so this acts as a floor
# without interfering with the CPU-based control of SYS_FAN1/2 and CPU0_FAN.
#
# Fan layout:
#   CPU0_FAN, SYS_FAN1, SYS_FAN2 — CPU side, BMC-controlled (cpuFanCurve)
#   SYS_FAN3, SYS_FAN4, SYS_FAN5 — drive side, controlled by this script
#
# Curve approach — STEP algorithm (this file):
#   Discrete duty levels with hysteresis. Fans hold a fixed speed until temp
#   crosses a threshold by more than STEP_DOWN_MARGIN, preventing hunting.
#   Pros: stable, easy to reason about. Cons: abrupt transitions between steps.
#
# Alternative — LINEAR slope (see sloped-fan-curve.py):
#   Continuous interpolation between breakpoints. Smoother transitions but
#   prone to hunting at boundary temps without an additional hysteresis dead-band.
#   To A/B test: copy sloped-fan-curve.py over this file and run the playbook.

import base64, json, os, re, ssl, subprocess, sys, urllib.request

BMC_URL      = os.environ["BMC_URL"]
BMC_USER     = os.environ["BMC_USER"]
BMC_PASS     = os.environ["BMC_PASS"]
PROFILE_URL  = f"{BMC_URL}/redfish/v1/Chassis/Self/Thermal/FanprofileService/Fanprofile"
TEXTFILE_PATH = "/var/lib/prometheus/node-exporter/hdd-fan-curve.prom"
BASE_PATH    = "/etc/hdd-fan-curve/base-profile.json"
LAST_STEP_PATH = "/etc/hdd-fan-curve/last-step"

HDD_DEVS     = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
SYS_FANS     = [164, 165, 166]
PROFILE_NAME = "cpuFanCurve"

# Step curve: (step-up threshold °C, duty %)
# Fan steps UP when temp rises above a threshold.
# Fan steps DOWN only when temp falls STEP_DOWN_MARGIN °C below the previous threshold.
# This prevents hunting at boundaries.
#
# Previous linear curve (kept for A/B comparison — see sloped-fan-curve.py):
#   CURVE = [(30, 30), (38, 45), (45, 70), (55, 100)]  # (°C, duty%) breakpoints
STEPS = [
    (40,  25),   # ≤40°C       → 25%  (quiet idle)
    (46,  45),   # 40–46°C     → 45%  (warming under load)
    (51,  72),   # 46–51°C     → 72%  (approaching concern zone)
    (999, 100),  # >51°C       → 100% (full blast)
]
STEP_DOWN_MARGIN = 2  # °C below step-up threshold before stepping down


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _auth():
    return "Basic " + base64.b64encode(f"{BMC_USER}:{BMC_PASS}".encode()).decode()

def bmc_get(url):
    req = urllib.request.Request(url, headers={"Authorization": _auth()})
    with urllib.request.urlopen(req, context=_ctx()) as r:
        return json.loads(r.read())

def bmc_put(url, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
          headers={"Authorization": _auth(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=_ctx()) as r:
        return json.loads(r.read())

def hdd_drive_data():
    drives = []
    for dev in HDD_DEVS:
        try:
            out_a = subprocess.check_output(["smartctl", "-A", dev],
                                            text=True, stderr=subprocess.DEVNULL)
            temp = None
            for line in out_a.splitlines():
                if re.search(r'\b194\b', line):
                    temp = int(line.split()[9])
                    break
            out_i = subprocess.check_output(["smartctl", "-i", dev],
                                            text=True, stderr=subprocess.DEVNULL)
            model = serial = ""
            for line in out_i.splitlines():
                if line.startswith("Device Model:"):
                    model = line.split(":", 1)[1].strip().replace(" ", "_")
                elif line.startswith("Serial Number:"):
                    serial = line.split(":", 1)[1].strip()
            if temp is not None:
                drives.append({"dev": dev, "model": model, "serial": serial, "temp": temp})
        except Exception:
            pass
    return drives

def get_last_step():
    try:
        with open(LAST_STEP_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None

def save_last_step(idx):
    with open(LAST_STEP_PATH, "w") as f:
        f.write(str(idx))

def compute_step(temp, last_idx):
    n = len(STEPS)
    if last_idx is None:
        # Cold start: pick initial step directly from temp
        for i, (threshold, _) in enumerate(STEPS):
            if temp <= threshold:
                return i
        return n - 1

    # Step up: jump immediately to the correct step if temp exceeds thresholds
    idx = last_idx
    while idx < n - 1 and temp > STEPS[idx][0]:
        idx += 1
    if idx != last_idx:
        return idx

    # Step down: one step at a time, only past the hysteresis margin
    if last_idx > 0 and temp <= STEPS[last_idx - 1][0] - STEP_DOWN_MARGIN:
        return last_idx - 1

    return last_idx

def write_metrics(drives, duty):
    lines = [
        "# HELP hdd_temperature_celsius HDD temperature from SMART attribute 194",
        "# TYPE hdd_temperature_celsius gauge",
    ]
    for d in drives:
        lines.append(
            f'hdd_temperature_celsius{{drive="{d["dev"]}",model="{d["model"]}",serial="{d["serial"]}"}} {d["temp"]}'
        )
    lines += [
        "",
        "# HELP hdd_fan_duty_percent Computed HDD fan duty cycle percent",
        "# TYPE hdd_fan_duty_percent gauge",
        f"hdd_fan_duty_percent {duty}",
        "",
    ]
    tmp = TEXTFILE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, TEXTFILE_PATH)

def main():
    if not os.path.exists(BASE_PATH):
        print("First run: saving clean base profile...")
        profile = bmc_get(PROFILE_URL)
        with open(BASE_PATH, "w") as f:
            json.dump(profile, f, indent=2)
        print(f"Saved {BASE_PATH} — re-run to apply HDD curve.")
        return

    drives = hdd_drive_data()
    if not drives:
        print("ERROR: could not read any HDD temps", file=sys.stderr)
        sys.exit(1)

    temp = max(d["temp"] for d in drives)
    last_idx = get_last_step()
    step_idx = compute_step(temp, last_idx)
    duty = STEPS[step_idx][1]

    if step_idx != last_idx:
        direction = "UP" if last_idx is None or step_idx > last_idx else "DOWN"
        print(f"HDD temps — max={temp}°C → step {step_idx} ({direction}), duty={duty}%")
        save_last_step(step_idx)
    else:
        print(f"HDD temps — max={temp}°C → step {step_idx} (stable), duty={duty}%")

    with open(BASE_PATH) as f:
        profile = json.load(f)

    custom = next(p for p in profile["arrProfile"] if p["strName"] == PROFILE_NAME)
    sys_fans_set = set(SYS_FANS)
    custom["arrPolicy"] = [
        p for p in custom["arrPolicy"]
        if not set(p.get("arrFanSensor", [])).issubset(sys_fans_set)
    ]
    custom["arrPolicy"].append({
        "arrDuty":           [duty, duty],
        "arrFanSensor":      SYS_FANS,
        "arrHexDeviceID":    [],
        "arrHexVendorID":    [],
        "arrRef":            [0, 1],
        "arrSensor":         [1],
        "iAmbientSensor":    0,
        "iAmbientSensorTemp": 0,
        "iCpuTdp":           0,
        "iHysteresis":       0,
        "iInSDR":            1,
        "iInitDuty":         duty,
        "iPCIEDeviceEnable": 0,
        "iPolicyType":       2,
        "iSensorCode":       1,
    })

    bmc_put(PROFILE_URL, profile)
    print(f"Profile updated: SYS fan floor = {duty}% (HDD max {temp}°C)")

    write_metrics(drives, duty)
    print(f"Metrics written to {TEXTFILE_PATH}")

if __name__ == "__main__":
    main()
