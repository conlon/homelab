# Homelab Claude Instructions

## FluxCD

This repo is managed by FluxCD. Any file changes under `fluxcd/` must be
**committed and pushed** to take effect. If changes are only written locally,
FluxCD will overwrite them on its next reconcile interval (default: 5m for
HelmReleases). Always commit after editing manifests here.

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
