#!/usr/bin/env python3
import base64, json, os, ssl, urllib.request

BMC_URL      = os.environ["BMC_URL"]
BMC_USER     = os.environ["BMC_USER"]
BMC_PASS     = os.environ["BMC_PASS"]
THERMAL_URL  = f"{BMC_URL}/redfish/v1/Chassis/Self/Thermal"
TEXTFILE_PATH = "/var/lib/prometheus/node-exporter/bmc-fan-metrics.prom"

def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _auth():
    return "Basic " + base64.b64encode(f"{BMC_USER}:{BMC_PASS}".encode()).decode()

def main():
    req = urllib.request.Request(THERMAL_URL, headers={"Authorization": _auth()})
    with urllib.request.urlopen(req, context=_ctx()) as r:
        data = json.loads(r.read())

    lines = [
        "# HELP bmc_fan_rpm Fan speed in RPM from BMC Redfish Thermal",
        "# TYPE bmc_fan_rpm gauge",
    ]
    for entry in data.get("Fans", []):
        name = entry.get("Name") or entry.get("MemberID", "")
        rpm  = entry.get("Reading")
        if name and rpm is not None:
            lines.append(f'bmc_fan_rpm{{fan="{name}"}} {rpm}')
    lines.append("")

    tmp = TEXTFILE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, TEXTFILE_PATH)

if __name__ == "__main__":
    main()
