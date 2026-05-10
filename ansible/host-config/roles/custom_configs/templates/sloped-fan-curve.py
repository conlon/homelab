#!/usr/bin/env python3
# HDD temperature fan curve for prox0 — linear slope algorithm (reference/A-B)
#
# This is the slope-based alternative to hdd-fan-curve.py.
# See that file for full context on the problem and Redfish injection approach.
#
# Curve approach — LINEAR slope (this file):
#   Continuously interpolates duty between (°C, duty%) breakpoints. Transitions
#   are smooth but the script PUTs a new duty to the BMC on every run, meaning
#   small temp fluctuations cause constant fan speed adjustments (hunting).
#   Adding a duty hysteresis dead-band (only update if duty shifts by ≥N%) would
#   reduce hunting but adds complexity.
#
# Active alternative — STEP algorithm (see hdd-fan-curve.py):
#   Discrete duty levels with built-in hysteresis. Fans hold a fixed speed until
#   temp crosses a threshold meaningfully. More stable at the cost of abrupt steps.
#   To switch back to this file: copy it over hdd-fan-curve.py and run the playbook.

import base64, json, os, re, ssl, subprocess, sys, urllib.request

BMC_URL   = os.environ["BMC_URL"]
BMC_USER  = os.environ["BMC_USER"]
BMC_PASS  = os.environ["BMC_PASS"]
PROFILE_URL = f"{BMC_URL}/redfish/v1/Chassis/Self/Thermal/FanprofileService/Fanprofile"
TEXTFILE_PATH = "/var/lib/prometheus/node-exporter/hdd-fan-curve.prom"
BASE_PATH = "/etc/hdd-fan-curve/base-profile.json"

HDD_DEVS     = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
SYS_FANS     = [164, 165, 166]
PROFILE_NAME = "cpuFanCurve"

# (°C, duty%) breakpoints — linear interpolation between points
CURVE = [(30, 30), (38, 45), (45, 70), (55, 100)]


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

def interpolate(temp):
    if temp <= CURVE[0][0]:  return CURVE[0][1]
    if temp >= CURVE[-1][0]: return CURVE[-1][1]
    for (t0, d0), (t1, d1) in zip(CURVE, CURVE[1:]):
        if t0 <= temp <= t1:
            return round(d0 + (d1 - d0) * (temp - t0) / (t1 - t0))

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
    duty = interpolate(temp)
    print(f"HDD temps — max={temp}°C → duty={duty}%")

    with open(BASE_PATH) as f:
        profile = json.load(f)

    custom = next(p for p in profile["arrProfile"] if p["strName"] == PROFILE_NAME)
    # Remove any stale HDD-only policies from previous runs before appending fresh one
    sys_fans_set = set(SYS_FANS)
    custom["arrPolicy"] = [
        p for p in custom["arrPolicy"]
        if not set(p.get("arrFanSensor", [])).issubset(sys_fans_set)
    ]
    custom["arrPolicy"].append({
        "arrDuty":         [duty, duty],
        "arrFanSensor":    SYS_FANS,
        "arrHexDeviceID":  [],
        "arrHexVendorID":  [],
        "arrRef":          [0, 1],      # dummy range; duty is fixed
        "arrSensor":       [1],         # CPU0_TEMP (required field; duty is fixed so value irrelevant)
        "iAmbientSensor":  0,
        "iAmbientSensorTemp": 0,
        "iCpuTdp":         0,
        "iHysteresis":     0,
        "iInSDR":          1,
        "iInitDuty":       duty,
        "iPCIEDeviceEnable": 0,
        "iPolicyType":     2,
        "iSensorCode":     1,
    })

    bmc_put(PROFILE_URL, profile)
    print(f"Profile updated: SYS fan floor = {duty}% (HDD max {temp}°C)")

    write_metrics(drives, duty)
    print(f"Metrics written to {TEXTFILE_PATH}")

if __name__ == "__main__":
    main()
