#!/usr/bin/env python3
# Longhorn stale-mount detector for the k3s nodes (Pis and VMs).
#
# Problem: Longhorn's v1 engine exposes each volume to the node as an iSCSI LUN
# (/dev/sdX, symlinked to /dev/longhorn/<volume>), and kubelet bind-mounts it by
# *device number* at NodeStage time. When the engine restarts — node reboot,
# instance-manager restart, engine crash under CPU/IO pressure — Longhorn tears
# the LUN down and recreates it with a NEW /dev/sdX. kubelet's mount still points
# at the old dev_t, which no longer exists, so every I/O returns EIO.
#
# Longhorn keeps reporting the volume "healthy" throughout (it tracks replica
# health, not the node's view of the device), and upstream documents that it
# cannot self-heal this case:
#   https://longhorn.io/docs/1.12.0/high-availability/recover-volume/
#   "Automatic Volume Remounting cannot change it back to read-write because the
#    device is now write-protected. In this case, you can only rely on the
#    Automatic Workload Pod Deletion mechanism."
# That mechanism is enabled here and still failed for overseerr on 2026-09-03,
# because deleting the pod is not enough: the pod is recreated on the same node
# and re-binds the SAME stale globalmount. NodeUnstage only runs once no pod on
# the node uses the volume. So nothing detects or fixes this on its own.
#
# Apps surface it as data corruption, which is why it burns hours every time:
# overseerr logged "SQLITE_CORRUPT: database disk image is malformed" for ~38h
# while its database was provably intact (PRAGMA integrity_check -> ok).
#
# Solution: every 60s, walk /proc/self/mountinfo for mounts backed by
# /dev/longhorn/*, and check whether each mount's device number still exists in
# /sys/dev/block/. If it doesn't, the mount is a zombie. Written to the
# node-exporter textfile collector, which the in-cluster kube-prometheus-stack
# node-exporter DaemonSet already mounts (see fluxcd/clusters/pi/prometheus/helm.yaml),
# so no extra Prometheus target is needed.
#
# Recovery when this fires is in docs/longhorn-stale-mount.md. A pod *restart*
# does NOT fix it — the workload must be scaled to 0 so the volume fully detaches.
#
# Metrics:
#   longhorn_stale_mount{volume="...",device="8:208",node="<hostname>"} 1
#   longhorn_stale_mount_count{node="<hostname>"} <number of stale volumes>
#   longhorn_stale_mount_scrape_success{node="<hostname>"} 1

import os
import socket

TEXTFILE_DIR = "/var/lib/node_exporter/textfile_collector"
TEXTFILE_PATH = os.path.join(TEXTFILE_DIR, "longhorn-stale-mount-exporter.prom")
MOUNTINFO = "/proc/self/mountinfo"
DEV_PREFIX = "/dev/longhorn/"
NODE = socket.gethostname()


def longhorn_mounts():
    """Yield (volume, dev_t, mountpoint) for every mount backed by /dev/longhorn/*.

    /proc/self/mountinfo fields:
      0=mount-id 1=parent-id 2=major:minor 3=root 4=mountpoint 5=options
      6..=optional fields, terminated by a literal "-", then fstype, source, super-opts
    """
    try:
        with open(MOUNTINFO) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return

    for line in lines:
        parts = line.split()
        try:
            sep = parts.index("-")
        except ValueError:
            continue
        # source is the field after the separator and fstype
        if len(parts) < sep + 3:
            continue
        source = parts[sep + 2]
        if not source.startswith(DEV_PREFIX):
            continue
        yield source[len(DEV_PREFIX):], parts[2], parts[4]


def main():
    # A volume is stale if ANY of its mounts points at a device that is gone.
    # Both the CSI globalmount and the per-pod bind mount share one dev_t, so
    # collapse to one series per volume to keep the alert readable.
    stale = {}
    for volume, dev, _mountpoint in longhorn_mounts():
        gone = not os.path.exists(os.path.join("/sys/dev/block", dev))
        # Once stale, stay stale — never let a healthy sibling mount mask it.
        if volume not in stale or gone:
            stale[volume] = (dev, gone)

    lines = [
        "# HELP longhorn_stale_mount Longhorn mount whose backing device no longer exists (1 = stale, needs scale-to-0 remount)",
        "# TYPE longhorn_stale_mount gauge",
    ]
    for volume in sorted(stale):
        dev, gone = stale[volume]
        lines.append(
            f'longhorn_stale_mount{{volume="{volume}",device="{dev}",node="{NODE}"}} {1 if gone else 0}'
        )

    lines += [
        "# HELP longhorn_stale_mount_count Number of Longhorn volumes on this node with a dead backing device",
        "# TYPE longhorn_stale_mount_count gauge",
        f'longhorn_stale_mount_count{{node="{NODE}"}} {sum(1 for _, gone in stale.values() if gone)}',
        "# HELP longhorn_stale_mount_scrape_success Whether the stale-mount check completed",
        "# TYPE longhorn_stale_mount_scrape_success gauge",
        f'longhorn_stale_mount_scrape_success{{node="{NODE}"}} 1',
        "",
    ]

    os.makedirs(TEXTFILE_DIR, exist_ok=True)
    tmp = TEXTFILE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, TEXTFILE_PATH)


if __name__ == "__main__":
    main()
