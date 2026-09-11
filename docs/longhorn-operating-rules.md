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

## Rule 1a — Use the DOCUMENTED eviction procedure, throttled

Source: [Longhorn 1.6.4 — Disks or Nodes Eviction](https://longhorn.io/docs/1.6.4/nodes-and-volumes/nodes/disks-or-nodes-eviction/)
and [Settings reference](https://longhorn.io/docs/1.6.4/references/settings/).

**Do not hand-roll replica moves.** Setting `evictionRequested: true` on an individual
*Replica* CR does not work — Longhorn's node controller silently cancels it within
seconds (`"Cancelling replica eviction" NodeController.syncReplicaEvictionRequested`).
Eviction is only honoured at **node or disk** level.

The documented procedure, with the throttle that makes it safe here:

1. **Verify capacity** on the remaining schedulable nodes, and confirm they can sustain
   the I/O (Rule 2 — free space is not enough).
2. **Throttle first.** Set `concurrentReplicaRebuildPerNodeLimit` to **1**
   (default is 5; this cluster ships 2). The docs state this setting *"controls how many
   replicas on a node can be rebuilt simultaneously"* and that it **affects eviction**.
   This is the supported knob for making an eviction gradual — use it instead of
   improvising.
3. **Disable scheduling** on the source node/disk. This is a documented *prerequisite*:
   *"This eviction feature can only be enabled when the selected disks or nodes have
   scheduling disabled."*
4. **Set `evictionRequested: true`** on the node or disk.
5. Longhorn handles per-volume safety itself: *"Longhorn only evicts a replica per volume
   after the replica rebuild for this volume is a success."* It does **not** limit how
   many volumes move concurrently — that is what step 2 controls.
6. **Watch target-node load throughout.** Abort (set `evictionRequested: false`) if any
   target exceeds its core count in load average. Eviction is resumable.
7. **Restore** `concurrentReplicaRebuildPerNodeLimit` afterwards.

> Even at limit=2, evacuating k3 drove k5 to load 23.75. On Pi-class nodes use **1**.

## Rule 1b — Every replica-set change is a hazard, not just bulk ones

Changing a volume's replica set while it is under write load can make the engine return
an unrecoverable **medium error** to the client, which aborts the ext4 journal and
remounts the filesystem read-only.

Confirmed three times in this cluster, same signature each time
(`critical medium error ... op 0x1:(WRITE)` → `Detected aborted journal` → `Remounting
filesystem read-only`):

| When | Volumes | Trigger |
|---|---|---|
| 2026-09-05 23:56 | 6 volumes | k1/k2 replicas vanished (speculative smartctl probing) |
| 2026-09-10 00:32 | uptime, sonarr-4k | replica churn |
| 2026-09-11 14:02 | authentik-database-2 | a deliberate, single-volume replica move |

The third was a careful, one-volume-at-a-time operation on a healthy volume and it still
happened. **So "do it gradually" is necessary but not sufficient.**

Per the Longhorn KB [`volume readonly or I/O error`](https://longhorn.io/kb/troubleshooting-volume-readonly-or-io-error/),
engine crashes of this kind come from lost replica connections caused by CPU starvation,
insufficient network bandwidth, latency, or slow disks. Longhorn's own
[best practices](https://longhorn.io/docs/1.6.4/best-practices/) note that
*"latency plays a much more important role in volume stability than IOPS or throughput"*
and recommend **10 Gbps between nodes** and a **dedicated storage network** — this
cluster has 1 Gbps shared with application traffic. We are operating below the
documented baseline, so treat every replica movement as carrying real risk.

**Therefore: only move replicas when there is a concrete reason.** Do not rebalance for
tidiness. Prefer scheduling constraints that prevent bad placement over moves that
correct it after the fact.

**Note this is NOT the known COW corruption bug** ([KB](https://longhorn.io/kb/analysis-filesystem-corrupted-issues-due-to-error-on-cow-while-rebuilding-replicas/)):
that affects v1.1.x–v1.3.1 (fixed in v1.2.6/v1.3.2), and its symptom is corrupted inodes
discovered after a rebuild, not medium errors during one. This cluster runs v1.6.4.

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

- [ ] How many replicas will move? (**>2 → stop, throttle it per Rule 1a**)
- [ ] Is `concurrentReplicaRebuildPerNodeLimit` set to 1 (Pi targets) before starting?
- [ ] Is there a concrete reason to move these at all? (Rule 1b — every move is a hazard)
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


---

## Where this cluster sits against Longhorn's documented baseline

From [best practices](https://longhorn.io/docs/1.6.4/best-practices/) — worth knowing
before blaming Longhorn for instability:

| Longhorn recommends | This cluster |
|---|---|
| 10 Gbps between nodes | **1 Gbps**, shared with application traffic |
| Dedicated storage network | **None** — `storage-network` is empty |
| SSD/NVMe; *"latency matters more than IOPS or throughput"* | VMs on NVMe (good); Pis on **USB-attached** SSDs |
| 4 vCPU / 4 GiB per node minimum | Pis are 4 core / 4–8 GiB — at the floor |
| `replicaAutoBalance: least-effort` | `best-effort` (more churn than recommended) |
| Default replica count 2 for lower system impact | StorageClass forces **3** |

None of these are fatal, but together they mean replica operations carry more risk here
than the documentation assumes. That is the context for Rules 1, 1a and 1b.

---

## Hardware: minimum and recommended

From [Longhorn 1.6.4 best practices](https://longhorn.io/docs/1.6.4/best-practices/),
with this cluster measured against it.

### Longhorn's stated minimum

| | Minimum |
|---|---|
| Nodes | 3 |
| CPU | 4 vCPU per node |
| Memory | 4 GiB per node |
| Disk | SSD/NVMe or equivalent block device (HDD supported but discouraged) |

### What actually determines stability

Longhorn is explicit that **latency, not throughput, is what matters**:

> *"latency plays a much more important role in volume stability than IOPS or throughput"*

This is the single most useful sentence in their docs for this cluster, and it is borne
out by measurement here. n2 showed 29% I/O pressure while moving only 183 KB/s; k5 hit
load 23.75 doing 2.6 MB/s. In both cases throughput was trivial and **latency** was the
problem. Do not reason about storage load in MB/s — reason about I/O wait
(`/proc/pressure/io`, D-state process counts, `await`).

Corollary: on a Pi the cost of a volume is **per-replica overhead** (snapshot files,
metadata, checksums) × replica count, largely independent of how busy the volume is.
On k5, 15 of its 22 volumes wrote *nothing at all* and still contributed. Judge a node by
**replica count**, not aggregate write rate.

### Network

| | Recommended | Here |
|---|---|---|
| Bandwidth between nodes | **10 Gbps** | 1 Gbps |
| Storage network | **Dedicated** | shared with application traffic (`storage-network` empty) |

Longhorn's KB also notes 1 Gbps serves roughly 3 volumes *under heavy load*. That figure
does not apply to idle volumes — measured steady-state across all 37 volumes here is
~340 KB/s, under 1% of the link — but it does apply during rebuilds, which is exactly
when things break.

### This cluster, per node

| Node type | CPU / RAM | Storage | Verdict |
|---|---|---|---|
| VMs (n1, n2, kami, kyoko) | 3–4 vCPU / 4–30 GiB | dedicated virtual disk on NVMe (Samsung 990 PRO) | Meets recommendations |
| Pi 4 (k1, k2) | 4 / 4 GiB | **Lexar USB flash drive**, no SMART | Below minimum — replace |
| Pi (k3) | 4 / 8 GiB | **SanDisk pSSD**, 0% spare blocks, SMART FAILED | Failed — replace |
| Pi 4/5 (k0, k4, k5) | 4 / 4–8 GiB | SanDisk SSD PLUS 240GB via USB | At the floor; workable |

**Pi nodes share one USB controller between the NIC and the SSD.** Throughput on one
starves the other — already documented for qbit (40–80 MB/s saturating the bus and taking
nodes down). This is why Pis tolerate replicas poorly even when CPU and disk look idle.

### Buying rule for Pi boot/storage media

"Solid state flash drive" is marketing, not a specification. The test that matters is
**SAT passthrough**, because no SMART means no health telemetry at all:

```sh
smartctl -d sat -i /dev/sdX   # must return a "Device Model:" line
smartctl -d sat -A /dev/sdX   # must include attribute 194 Temperature_Celsius
```

If `smartctl --scan` finds nothing, return it — that is the Lexar signature. Prefer a
real SSD in a known-good USB bridge (ASMedia ASM1153/ASM235CM, JMicron JMS578/583,
Realtek RTL9210). **Never** iterate speculative `-d` types to find one that works
(Rule 10).

### Practical implication

This cluster runs below the documented baseline on network bandwidth, storage network
isolation, and Pi disk latency simultaneously. Longhorn still works — no data has been
lost across three incidents — but **replica operations carry more risk here than the
documentation assumes**. That is the justification for Rules 1, 1a and 1b, and the reason
placement policy (keeping replicas off weak nodes) is worth more than any amount of
careful manual rebalancing.
