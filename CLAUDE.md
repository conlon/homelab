# Homelab Claude Instructions

## FluxCD

This repo is managed by FluxCD. Any file changes under `fluxcd/` must be
**committed and pushed** to take effect. If changes are only written locally,
FluxCD will overwrite them on its next reconcile interval (default: 5m for
HelmReleases). Always commit after editing manifests here. To force an immediate reconcile after pushing:
```sh
flux reconcile helmrelease <name> -n <namespace>
```

### Testing significant changes

For non-trivial changes, verify before committing:

1. **Suspend FluxCD reconciliation** for the affected resource:
   ```sh
   flux suspend helmrelease <name> -n <namespace>
   ```
2. **Apply manually** to the cluster:
   ```sh
   kubectl apply -f fluxcd/clusters/pi/home/<app>/
   # or for Helm values changes, use helm upgrade directly
   ```
3. **Verify** the change behaves as expected.
4. **Commit and push** the change.
5. **Resume FluxCD**:
   ```sh
   flux resume helmrelease <name> -n <namespace>
   ```

## Cluster access (SSH + kubeconfig)

Node inventory — names → IPs and the `master`/`node`/`vm`/`nas` groups — lives in
`ansible/k3s-config/inventory/k8s/hosts.yml`. Check it before guessing hosts.

- **SSH user is `pi`** for the Raspberry Pi nodes (k0–k5), with passwordless
  sudo. VMs (ava, kami, kyoko, n0/n1/n2) may use a different user. The NAS is
  `michael@truenas:4242`.
- **The API endpoint `192.168.86.19:6443` is a kube-vip VIP — no host owns it.**
  The real control-plane nodes are **k0 (.20), ava (.7), n0 (.26)**
  (`kubectl get nodes -l node-role.kubernetes.io/control-plane`); k3s runs there,
  act on k0. `kyoko` is a worker, not a master.
- **Expired kubeconfig** (symptom: `the server has asked for the client to provide
  credentials`) → runbook at `docs/k3s-cluster-access.md`. Flux is unaffected (it
  uses an in-cluster ServiceAccount), so reconciliation continues.
