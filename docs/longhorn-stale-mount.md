# Longhorn stale mount ("the corruption that isn't")

**Symptom:** an app reports data corruption or I/O errors — `SQLITE_CORRUPT: database disk
image is malformed`, `Input/output error`, `ls: reading directory: I/O error` — while Longhorn
reports the volume **`attached` / `healthy`** and every replica is running.

**It is almost certainly not corruption.** Check before doing anything destructive.

## Diagnosis (read-only, 30 seconds)

Find the node the volume is attached to:

```sh
kubectl get volumes.longhorn.io -n longhorn-system <vol> \
  -o jsonpath='{.status.state}{" "}{.status.robustness}{" node="}{.status.currentNodeID}{"\n"}'
```

On that node, compare the mount's device number against the devices that actually exist:

```sh
grep <vol> /proc/self/mountinfo     # 3rd field is the dev_t, e.g. 8:208
ls /sys/dev/block/8:208             # if this does NOT exist, the mount is a zombie
```

Corroborating evidence in `dmesg -T`:

```
node: attempt to access beyond end of device
      sdn: rw=12288, sector=120, nr_sectors = 8 limit=0     <- limit=0 means the device is GONE
EXT4-fs warning (device sdn): ... error -5 reading directory block
```

`limit=0` is the tell: the block device size reads as zero because the device no longer exists.
This is **not** ext4 damage. Compare with `docs/`-documented real filesystem corruption, where
`e2fsck` finds actual errors — see the fsck procedure if `e2fsck -fn` reports genuine problems
*after* you have fixed the mount.

Also check whether Longhorn snapshots taken since the incident have **size 0** — that confirms
nothing has been written since the device died, so the replica data is intact.

## Why it happens

Longhorn's v1 engine exposes each volume as an iSCSI LUN → `/dev/sdX` → `/dev/longhorn/<vol>`.
kubelet bind-mounts it **by device number** at NodeStage. When the engine restarts — node reboot,
instance-manager restart, or an engine crash under CPU/IO pressure — Longhorn tears the LUN down
and recreates it as a **new** `/dev/sdX`. kubelet's mount still points at the old one.

Longhorn tracks replica health, not the node's view of the device, so it keeps reporting
`healthy` the entire time.

## Why the obvious fixes don't work

- **Restarting the pod does not fix it.** The pod is recreated on the same node and bind-mounts
  the *same stale globalmount*. `NodeUnstage` only runs once **no** pod on that node uses the
  volume. Overseerr restarted 3 times on 2026-09-03 and stayed broken for ~38h.
- **`auto-delete-pod-when-volume-detached-unexpectedly` does not save you.** It is enabled in this
  cluster and still failed, for the same reason. Upstream documents the limitation:
  <https://longhorn.io/docs/1.12.0/high-availability/recover-volume/>
- **Do not restore from backup.** The data on the replicas is fine and is *newer* than any backup.
  Restoring into a fresh volume trades away real data to fix a mount problem — and per
  `docs/longhorn-migration-retrospective.md` §4/§5, restore-and-repoint has its own sharp edges.

## Fix

Scale the workload to **0** so the volume fully detaches and `NodeUnstage` runs.

```sh
# 1. Flux pins replicas: 1, so suspend it first
flux suspend kustomization flux-system -n flux-system

# 2. Remove the pod entirely
kubectl scale statefulset <app> -n <ns> --replicas=0

# 3. Wait for the volume to detach, and confirm the node is clean
kubectl get volumes.longhorn.io -n longhorn-system <vol> -o jsonpath='{.status.state}{"\n"}'
#    on the node:
grep <vol> /proc/self/mountinfo || echo "mounts clean"
ls -l /dev/longhorn/<vol> 2>/dev/null || echo "stale device node cleared"

# 4. Optional, while detached: bring the engine up to the manager's version
kubectl patch volumes.longhorn.io -n longhorn-system <vol> --type=merge \
  -p '{"spec":{"image":"longhornio/longhorn-engine:v1.6.4"}}'

# 5. Bring it back — CSI does a fresh NodeStage against the live device
kubectl scale statefulset <app> -n <ns> --replicas=1

# 6. Resume Flux
flux resume kustomization flux-system -n flux-system
```

If the pod hangs in `Terminating` on the dead mount, lazily unmount on the node:
`sudo umount -l <globalmount path>`.

## Verify

```sh
kubectl exec -n <ns> <pod> -- ls -la /config          # should list without I/O errors
```

For SQLite apps, confirm the database really is fine rather than assuming:

```sh
kubectl exec -n <ns> <pod> -- node -e '
const s=require("/app/<app>/node_modules/sqlite3");
const db=new s.Database("/config/db/db.sqlite3", s.OPEN_READONLY);
db.all("PRAGMA integrity_check;", (e,r)=>console.log(e||JSON.stringify(r)));'
```

On 2026-09-03 overseerr returned `integrity_check: ok` with all 1456 media / 204 requests /
3 users intact, after ~38h of logging "database disk image is malformed".

## Prevention / detection

`longhorn-stale-mount-exporter` runs on every k3s node every 60s and exports
`longhorn_stale_mount{volume,device,node}`. The `LonghornStaleMount` alert in
`fluxcd/clusters/pi/longhorn/prometheusrule.yaml` fires critical after 2m.

Reducing how often it triggers means reducing engine restarts — keep node CPU/IO pressure down
(check `/proc/pressure/io`), and keep CPU limits on a node under its capacity so the Longhorn
instance manager is not starved.
