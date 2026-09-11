# Longhorn operating rules — READ BEFORE ANY LONGHORN OPERATION

**These are rules, not guidance.** Every one exists because breaking it caused a
cascading failure in this cluster. Re-read this before touching Longhorn, every time.

---

## Rule 1 — Never move replicas in bulk. Ever.

**Maximum 2 replicas in flight cluster-wide. Maximum 1 per target node.**

Wait for `robustness: healthy` on every affected volume AND target-node load below
core-count before starting the next.

Longhorn will happily accept "evict this whole node" as one instruction and then move
everything at once. It does not throttle for you, and
`concurrentReplicaRebuildPerNodeLimit` does not protect the *source* or the cluster.

> **2026-09-11:** Requested eviction of all 16 replicas from k3. Longhorn moved 14 of
> them onto k5 within minutes. k5 reached load 23.75 (pure I/O wait), replicas stopped
> under the pressure, and **four database volumes dropped to a single copy** — the exact
> redundancy the evacuation was meant to protect. Recovery took longer than the original
> problem would have.
>
> **2026-06:** The same mistake during the n0/n1/n2 drain — all three nodes cordoned at
> once, instance managers evicted with nowhere to go, engine deadlock. A full day lost.
> See `longhorn-migration-retrospective.md`.

This mistake has now been made in at least three separate sessions. Assume you are
about to make it again.

## Rule 1a — Per-replica `evictionRequested` does NOT work. Use bump-delete-restore.

Setting `evictionRequested: true` on an individual Replica CR is **silently cancelled**
by Longhorn's node controller within seconds:

```
"Cancelling replica eviction" func="controller.(*NodeController).syncReplicaEvictionRequested"
```

Longhorn only honours eviction at the **node/disk** level — which moves *everything at
once* and violates Rule 1. So there is no built-in mechanism for a controlled,
one-at-a-time move.

**The safe technique, which never reduces the live copy count:**

1. `numberOfReplicas` N → N+1 — a spare builds on a healthy node (ensure the bad node is
   `allowScheduling: false` first, per Rule 2)
2. Wait until the engine reports **N+1 `RW`** replicas — not `running`, `RW`
3. Delete the replica on the node you are draining
4. `numberOfReplicas` back to N

At no point does the volume drop below N readable copies. Deleting first and letting it
rebuild does the opposite: it creates a single-copy window on a 2-replica volume.

If the spare never reaches `RW`, revert to N and leave the original in place. Do not
force it.

## Rule 2 — Free space is not a placement signal. Constrain targets FIRST.

Longhorn schedules replicas by **available disk space only**. It has no concept of
whether a node can sustain the I/O. The node with the most free space is frequently the
worst possible target.

**Before** starting any move, make unsuitable nodes unschedulable
(`allowScheduling: false`) — do not fix placement afterwards.

> k5 was chosen for 14 replicas because it had 184G free. It is a Pi with one USB SSD
> carrying the OS, containerd and 22 replicas. Measured: **2.6 MB/s of physical writes
> to serve 178 KB/s of application data.** On a Pi the cost is per-replica overhead
> (snapshot files, metadata, checksums) × replica count — 15 of those 22 volumes wrote
> nothing at all and still contributed.

## Rule 3 — Check redundancy before every destructive action

Before deleting/evicting any replica, or scaling any workload to 0, confirm via the
**engine's `replicaModeMap`** (not the replica CR's state) that the volume retains at
least one other `RW` replica.

`RW` = readable. `WO` = rebuilding, **not** a usable copy. `ERR` = failed.

> A replica showing `running` in its CR can be `WO` in the engine. On 2026-09-11
> immich-database showed two `running` replicas but only one was `RW` — the other was a
> rebuild being written *onto the overloaded node*. Deleting the wrong one would have
> left zero copies.

## Rule 4 — Never run `e2fsck` without proving the volume is unmounted

```sh
grep -c "<volume>" /proc/self/mountinfo    # MUST return 0
```

`e2fsck -fy` on a mounted filesystem causes real corruption. Scaling a workload to 0 is
**not** proof — the pod can sit in `Terminating` for minutes with mounts held, and the
Longhorn volume can still report `attached` with its original CSI attachment ticket.

> This guard caught a live mistake on 2026-09-08: a readiness check reported the volume
> detached while the pod was still `Terminating` and both mounts were held.

## Rule 5 — Suspend Flux before changing Longhorn Node CRDs, and commit the end state

Node CRDs live in `fluxcd/clusters/pi/longhorn/nodes/`. Flux reverts live changes on its
next reconcile. Either suspend first, or commit the change — never assume a `kubectl
patch` will survive.

Equally: **do not leave Flux suspended.** Resume it and verify the reconcile is a no-op.

## Rule 6 — Distinguish "stopped" from "failed"

A replica `stopped` with **no `failedAt`** will never be rebuilt and never cleaned up —
Longhorn's replenishment only fires on `failedAt`. It silently blocks the volume from
returning to full replica count.

Deleting it sets `failedAt` and triggers a rebuild. But first confirm Rule 3.

Exception: for a **detached** volume every replica is legitimately `stopped` with no
`failedAt`. That is normal, not a fault.

## Rule 7 — Get explicit approval, and report before acting

Per standing instruction: no `kubectl patch`/`delete`/`edit` on Longhorn CRDs, no
scaling to release volumes, no salvage or restore, without saying what will run and why,
and waiting for a yes.

For anything touching more than one volume, present an **itemised list** (volume, node,
replica count, what happens to each) before starting.

## Rule 8 — Longhorn reporting `healthy` means replicas are healthy. Nothing more.

It says nothing about:
- the **filesystem** inside the volume (`longhorn_fs_errors`, ext4 aborted journal)
- whether the node's **mount** still points at a live device (`longhorn_stale_mount`)
- whether the **application** can actually write

Always verify at the layer you actually care about. Eight volumes have been repaired in
this cluster while Longhorn reported every one of them `healthy` throughout.

## Rule 9 — Measure before concluding; correlation is not cause

Cheap, read-only, and available at all times:

| Question | Command |
|---|---|
| Real write rates | `/proc/diskstats` against `/dev/longhorn/*` |
| Node saturation | `/proc/pressure/{io,cpu}`, `/proc/loadavg`, D-state process count |
| Filesystem damage | `/sys/fs/ext4/<dev>/errors_count`, `dumpe2fs -h` |
| Stale mount | mount `dev_t` vs `/sys/dev/block/` |
| Drive health | `smartctl -H -A -d sat` (**only** the `-d` type `--scan` reports) |

> Three confident diagnoses in these sessions were wrong and corrected by measurement:
> "network saturation" (actual: 0.8% link utilisation), "73% remote replicas is a defect"
> (remote replicas are the design), and "k3's failing disk corrupted those filesystems"
> (k3 has never logged a single kernel I/O error, including under a 92G evacuation read).

## Rule 10 — Never probe storage hardware speculatively

Use **only** the `-d` type that `smartctl --scan` reports. Never iterate candidate types
hoping one works.

> **2026-09-06:** Iterating `usbjmicron`, `usbsunplus`, `usbasm1051` and others against
> k1 and k2 hung both USB bridges, dropped both nodes off the bus, and cascaded into six
> damaged filesystems. The drives were never faulty — a power cycle restored both.

---

## Pre-flight checklist

Before any Longhorn operation, answer all of these in writing:

- [ ] How many replicas will move? (**>2 → stop, batch it**)
- [ ] Which nodes can receive them, and are all unsuitable ones `allowScheduling: false`?
- [ ] For each affected volume: how many `RW` replicas remain *during* the operation?
- [ ] Is Flux suspended, or is the end state committed?
- [ ] What is the rollback if this goes wrong?
- [ ] Has the user approved this specific list?

## Post-flight verification

- [ ] All volumes `robustness: healthy` at desired replica count
- [ ] `sum(longhorn_fs_errors) == 0` and `sum(longhorn_stale_mount) == 0`
- [ ] No node above its core count in load average
- [ ] Applications can actually write (test, don't assume)
- [ ] Flux resumed and reconciling clean
