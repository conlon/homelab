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
#
# SECOND failure mode, added after radarr/radarr-priv on 2026-09-06: the device
# stays present and correct, but ext4 aborts its journal after an I/O error (e.g.
# replicas vanishing mid-write). The filesystem then fails every write with EIO
# while /proc/mounts still reports "rw", and Longhorn still reports the volume
# healthy — because the damage is in the filesystem, not the block device. The
# stale-mount check above cannot see this: the device is fine.
# ext4 latches this in the superblock and exposes a counter in sysfs, so:
#   longhorn_fs_errors{volume="...",device="sdg",node="..."} 1   (ext4 errors_count)
#   longhorn_fs_readonly{volume="...",device="sdg",node="..."} 1 (remounted ro)
# Recovery is the same shape as a stale mount — scale to 0 — but additionally
# needs e2fsck to replay the journal and clear the error flag.

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


def is_read_only(dev_t):
    """True if any mount of this device carries the 'ro' option."""
    try:
        with open(MOUNTINFO) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 5 and parts[2] == dev_t:
                    if "ro" in parts[5].split(","):
                        return True
    except OSError:
        pass
    return False


def ext4_state(dev_t):
    """Return (errors_count, device_name) for a mounted ext4 device, or (None, None).

    ext4 latches I/O errors in the superblock and exposes the count at
    /sys/fs/ext4/<dev>/errors_count. Non-zero means the filesystem hit an error
    and, if the journal aborted, is now refusing writes even though the mount
    still reports rw and the block device is perfectly healthy.
    """
    try:
        name = os.path.basename(os.path.realpath(os.path.join("/sys/dev/block", dev_t)))
    except OSError:
        return None, None
    try:
        with open(os.path.join("/sys/fs/ext4", name, "errors_count")) as fh:
            return int(fh.read().strip()), name
    except (OSError, ValueError):
        return None, name


def main():
    # A volume is stale if ANY of its mounts points at a device that is gone.
    # Both the CSI globalmount and the per-pod bind mount share one dev_t, so
    # collapse to one series per volume to keep the alert readable.
    stale = {}
    fs_state = {}   # volume -> (device_name, errors_count, read_only)
    for volume, dev, _mountpoint in longhorn_mounts():
        gone = not os.path.exists(os.path.join("/sys/dev/block", dev))
        # Once stale, stay stale — never let a healthy sibling mount mask it.
        if volume not in stale or gone:
            stale[volume] = (dev, gone)
        if not gone:
            errs, name = ext4_state(dev)
            if errs is not None:
                ro = is_read_only(dev)
                prev = fs_state.get(volume)
                # keep the worst reading across this volume's mounts
                if prev is None or errs > prev[1] or ro:
                    fs_state[volume] = (name, errs, ro)

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
        "# HELP longhorn_fs_errors ext4 errors_count for a Longhorn volume (>0 = filesystem hit an I/O error; writes may be failing while the volume looks healthy)",
        "# TYPE longhorn_fs_errors gauge",
    ]
    for volume in sorted(fs_state):
        name, errs, _ro = fs_state[volume]
        lines.append(f'longhorn_fs_errors{{volume="{volume}",device="{name}",node="{NODE}"}} {errs}')
    lines += [
        "# HELP longhorn_fs_readonly Longhorn volume whose filesystem was remounted read-only after an error",
        "# TYPE longhorn_fs_readonly gauge",
    ]
    for volume in sorted(fs_state):
        name, _errs, ro = fs_state[volume]
        lines.append(f'longhorn_fs_readonly{{volume="{volume}",device="{name}",node="{NODE}"}} {1 if ro else 0}')

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
