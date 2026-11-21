---
name: node-drain
description: Safely cordon, drain, and shut down a k3s node while keeping Longhorn volumes healthy. Use for planned node maintenance, hardware work, thermal mitigation, or Pi relocation. Verifies Longhorn data safety before evicting, watches volume health throughout, and covers bringing the node back.
argument-hint: "[node-name]"
disable-model-invocation: false
allowed-tools: Bash(kubectl *), Bash(ssh *), Bash(ping *)
---

# node-drain: Safely drain & shut down a k3s node (Longhorn-aware)

Take `$ARGUMENTS` out of service without degrading any Longhorn volume. Never
skip the pre-flight — the whole point is knowing the node holds no live volume
data *before* you evict anything.

Node inventory: `ansible/k3s-config/inventory/k8s/hosts.yml`. SSH user is `pi`
for the Pis (k0–k5) with passwordless sudo; VMs may differ. Act on a
control-plane node (k0/ava/n0) for kubectl — `.19:6443` is a kube-vip VIP owned
by no host.

## Phase 1 — Pre-flight: is the node data-safe to drain?

Confirm the node holds **zero Longhorn replicas AND zero engines**. Replicas =
stored data; engines = live attachments serving I/O. Checking only replicas
misses a volume that's actively attached through this node.

```sh
NODE=<node>
# Replicas physically on the node (stored data)
kubectl -n longhorn-system get replicas.longhorn.io -o json | python3 -c "
import json,sys; d=json.load(sys.stdin)
r=[i['spec']['volumeName'] for i in d['items'] if i['spec'].get('nodeID')=='$NODE']
print('replicas on $NODE:', len(r)); [print(' ',x) for x in r]"
# Engines on the node (live attachments)
kubectl -n longhorn-system get engines.longhorn.io -o json | python3 -c "
import json,sys; d=json.load(sys.stdin)
e=[i['spec']['volumeName'] for i in d['items'] if i['spec'].get('nodeID')=='$NODE']
print('engines on $NODE:', len(e)); [print(' ',x) for x in e]"
```

Confirm the drain policy is the safety net and note the current health baseline:
```sh
kubectl -n longhorn-system get settings.longhorn.io node-drain-policy
# want: block-for-eviction  (drain auto-evicts+rebuilds replicas before finishing)
kubectl -n longhorn-system get volumes.longhorn.io \
  -o custom-columns=NAME:.metadata.name,STATE:.status.state,ROBUSTNESS:.status.robustness
# every volume should be robustness=healthy BEFORE you start
```

Decision:
- **0 replicas + 0 engines** → data-safe. Drain is quick; nothing to migrate.
- **>0 replicas/engines** with `block-for-eviction` → still safe, but the drain
  **blocks** until Longhorn rebuilds those replicas elsewhere (can be many
  minutes on slow Pis / large volumes). Get user approval first — evicting
  replicas is a Longhorn data operation. Do NOT force past a blocked drain.
- **Any volume already non-healthy** → stop. Fix that first; don't remove a node
  while a volume is degraded.

## Phase 2 — Cordon & drain

```sh
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --timeout=300s
```

Expected to evict: real workload pods (Deployments/StatefulSets reschedule
elsewhere) and Longhorn's `instance-manager` (evicts instantly when it serves
no engines/replicas — that instant eviction confirms nothing depended on it).

Expected to STAY (daemonsets, ignored — they die with the node): `longhorn-manager`,
`longhorn-csi-plugin`, `engine-image-*`, `metallb speaker`, `node-exporter`.

## Phase 3 — Verify before pulling power

```sh
# No volume should have dropped out of healthy during eviction
kubectl -n longhorn-system get volumes.longhorn.io -o json | python3 -c "
import json,sys; d=json.load(sys.stdin)
bad=[i['metadata']['name'] for i in d['items'] if i['status'].get('robustness')!='healthy']
print('non-healthy:', len(bad), bad)"
# Evicted Deployments came back Ready on other nodes
kubectl get pods -A -o wide | grep -v Terminating | grep -Ei '<evicted-app-names>'
```
All volumes healthy + evicted apps Running elsewhere → safe to power off.

## Phase 4 — Shut down

```sh
ssh -o ConnectTimeout=10 pi@<node-ip> 'sudo shutdown -h now'
# exit 0, or 255/connection-closed as the box drops — both expected
```
Confirm it's actually down (shutdown takes a few seconds — poll, don't assume):
```sh
until ! ping -c1 -t2 <node-ip> >/dev/null 2>&1; do sleep 2; done; echo down
kubectl get node $NODE        # -> NotReady,SchedulingDisabled
```

## Phase 5 — Bringing it back

Power on, wait for `kubectl get node $NODE` = `Ready`, then:
```sh
kubectl uncordon $NODE
```
Longhorn reschedules replicas onto it over time; volumes that were healthy stay
healthy. Nothing else to do.

## Important rules

- **Check replicas AND engines** — replicas alone under-reports live attachments.
- **Baseline health first** — never drain while any volume is already degraded.
- A blocked `block-for-eviction` drain is Longhorn working as intended. **Wait it
  out; never `--force`** past it — that abandons replicas mid-rebuild.
- Evicting replicas (the >0 case) is a Longhorn data op → get user approval.
- `instance-manager` evicting instantly is the signal the node served no volumes.
