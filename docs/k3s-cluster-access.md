# k3s cluster access: topology, the API VIP, and cert rotation

Practical runbook for "why can't I reach the cluster" — covers the virtual API
endpoint (the thing with no host behind it) and recovering from an expired
kubeconfig cert. Written after the laptop admin cert expired on 2026-08-22.

## Topology — why there's no host at `192.168.86.19`

The kubeconfig points at `https://192.168.86.19:6443`, but **nothing pings
`.19`** — it's a **kube-vip virtual IP**, not a machine. kube-vip (deployed by
the `k3s-config` ansible role, `roles/k3s_server/templates/vip.yaml.j2`) runs a
DaemonSet on the control-plane nodes that advertises `.19` via ARP and keeps it
claimed by whichever master is currently healthy. This gives kubectl one stable
address that survives any single master going down.

- **API VIP (kube-vip):** `192.168.86.19` — the `apiserver_endpoint`, in every
  kubeconfig and in the server cert's `--tls-san`. **No host owns it.**
- **Control-plane (masters):** HA, 3 nodes on different hardware —
  **`k0` (.20, Pi), `ava` (.7, VM), `n0` (.26, VM)**. Confirmed against the live
  cluster (`control-plane,etcd,master`); matches `inventory/k8s/hosts.yml`'s
  `master:` group. Note `kyoko` (.9) is a **worker**, not a master (a common
  mis-recollection). Re-check any time with:
  ```sh
  kubectl get nodes -l node-role.kubernetes.io/control-plane -o wide
  ```
- **Workers:** the remaining Pis (k1–k5) and VMs (kami, kyoko, n1, n2).

Restarting k3s on **one** master is safe: the VIP floats to (or is re-claimed
by) another master, so the API stays reachable through `.19` during the blip.

## Symptom: "the server has asked for the client to provide credentials"

```
E... couldn't get current server API group list: the server has asked for
      the client to provide credentials
```

This is a **401 client-auth** failure, *not* a TLS error — the API server is up
and serving fine; it's rejecting your client cert. The usual cause is an
**expired admin client certificate** in your kubeconfig. k3s issues these for
365 days; the embedded copy in `~/.kube/config` does **not** auto-update when the
server rotates its own.

Confirm it's the cert (decode the embedded client cert and read its dates):

```sh
kubectl config view --raw -o jsonpath='{.users[0].user.client-certificate-data}' \
  | base64 -d | openssl x509 -noout -subject -dates
# notAfter in the past  ->  expired admin cert (this runbook)
```

`> Flux is unaffected.` Flux authenticates with an in-cluster ServiceAccount,
not your kubeconfig — reconciliation keeps running through the outage. This is a
*your-laptop* problem, not a cluster problem.

## Fix

Act on any master; `k0` (`192.168.86.20`) is the canonical one. Because the API
is usually still up (401, not a TLS failure), try the no-restart path first.

### 1. Check the server's own admin cert

```sh
ssh michael@192.168.86.20 \
  "sudo grep client-certificate-data /etc/rancher/k3s/k3s.yaml \
   | awk '{print \$2}' | base64 -d | openssl x509 -noout -dates"
```

### 2a. Server cert still valid → just refresh the laptop kubeconfig (no restart)

The server already holds a good cert; your laptop's copy is simply stale. Copy
it down, rewriting the loopback address `k3s.yaml` ships with to the **VIP**:

```sh
cp ~/.kube/config ~/.kube/config.bak-$(date +%Y%m%d)
ssh michael@192.168.86.20 "sudo cat /etc/rancher/k3s/k3s.yaml" \
  | sed 's/127.0.0.1/192.168.86.19/' > ~/.kube/config
kubectl get nodes
```

### 2b. Server cert also expired → rotate it, then copy

k3s regenerates certs that are expired or within 90 days of expiry on startup:

```sh
ssh michael@192.168.86.20 "sudo systemctl restart k3s"   # ~30s API blip
# fallback if a restart alone doesn't rotate:
#   ssh michael@192.168.86.20 "sudo k3s certificate rotate && sudo systemctl restart k3s"
```

Then run the copy from **2a**. Point kubectl at the VIP (`.19`), not a specific
master, so you keep HA failover.

## Notes

- The `k3s-config` role's kubeconfig task writes `/etc/rancher/k3s/k3s.yaml` to
  the *server node's* home and rewrites the endpoint — it does **not** rotate
  certs and does **not** touch your Mac. Re-running the playbook is not the fix.
- Certs expire ~yearly. If the laptop cert expired, the others likely will soon;
  a periodic `systemctl restart k3s` (or a scheduled rotation) keeps them fresh.
- SSH user for the Pis may differ; the NAS is `michael@truenas:4242`.
