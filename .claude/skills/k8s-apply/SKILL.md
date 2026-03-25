---
name: k8s-apply
description: Apply new or updated Kubernetes manifests to the cluster. Use when deploying a new app, updating a HelmRelease, or iterating on manifest configuration. Handles pausing FluxCD, applying and verifying changes manually, committing, then resuming reconciliation.
argument-hint: "[app-name] [namespace]"
disable-model-invocation: false
allowed-tools: Bash(kubectl *), Bash(flux *), Bash(git *)
---

# k8s-apply: Deploy/Update Kubernetes Manifests

Apply and verify changes to `$ARGUMENTS` in this cluster, following the safe FluxCD workflow.

## Repo layout

Manifests live under `fluxcd/clusters/pi/`. Common app paths:
- `fluxcd/clusters/pi/home/<app>/` — home-namespace apps (sonarr, qbit, immich, etc.)
- `fluxcd/clusters/pi/<component>/` — infrastructure (cert-manager, traefik, longhorn, etc.)

## Phase 1 — Suspend FluxCD reconciliation

Before touching any manifests, suspend reconciliation for the affected resource(s) so FluxCD doesn't overwrite your changes:

```sh
# Suspend a HelmRelease
flux suspend helmrelease <name> -n <namespace>

# Suspend a Kustomization (if editing raw manifests)
flux suspend kustomization <name> -n <namespace>
```

Confirm suspension:
```sh
flux get helmrelease <name> -n <namespace>
# Should show: Suspended: True
```

## Phase 2 — Edit and apply manifests iteratively

Edit the manifest files, then apply directly:

```sh
# Apply a single file
kubectl apply -f fluxcd/clusters/pi/home/<app>/<file>.yaml

# Apply the whole app directory
kubectl apply -f fluxcd/clusters/pi/home/<app>/

# For HelmRelease values changes: trigger an upgrade
flux reconcile helmrelease <name> -n <namespace>
```

### Verification steps after each apply

Check pod status — do NOT immediately delete pods after a reconcile:
```sh
kubectl get pods -n <namespace> -w
kubectl describe pod <pod> -n <namespace>
kubectl logs <pod> -n <namespace> --tail=50
```

For HelmRelease changes, wait for the upgrade to complete before assessing pod state:
```sh
flux get helmrelease <name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
# or for StatefulSets:
kubectl rollout status statefulset/<name> -n <namespace>
```

Only delete a pod manually if ALL of these are true:
1. `kubectl get statefulset <name> -n <namespace>` shows `updateRevision != currentRevision`
2. The pod is stuck in `CrashLoopBackOff` preventing the rolling update

### Common issues to check

- **OOMKilled**: check `kubectl describe pod` for memory limits — increase if needed
- **Image pull errors**: check `imagePullPolicy` and tag
- **Volume issues**: check PVC names match exactly (StatefulSets suffix PVCs with `-<index>`)
- **Node affinity**: bjw-s app-template doesn't support `nodeName` — use `nodeSelector: {kubernetes.io/hostname: <node>}` instead

## Phase 3 — Commit and push verified manifests

Once the configuration is verified working, commit all changed manifest files:

```sh
# Stage specific files (prefer over git add -A)
git add fluxcd/clusters/pi/home/<app>/

git commit -m "<type>(<app>): <description>"
git push
```

Commit message conventions from this repo:
- `feat(sonarr): add private instance`
- `fix(qbit): increase gluetun memory limit to 384Mi`
- `chore(deps): update helmrelease version`

## Phase 4 — Resume FluxCD reconciliation

After pushing, resume and let FluxCD take over:

```sh
flux resume helmrelease <name> -n <namespace>
# or
flux resume kustomization <name> -n <namespace>
```

Then trigger a full reconcile to confirm FluxCD picks up the pushed state cleanly:

```sh
flux reconcile source git flux-system
flux reconcile kustomization flux-system
# Only if the HelmRelease needs a forced re-run:
flux reconcile helmrelease <name> -n <namespace>
```

Verify FluxCD shows the resource as Ready:
```sh
flux get helmrelease <name> -n <namespace>
# Should show: Ready: True, Suspended: False
```

## Important rules

- **Never chain** `flux reconcile helmrelease` with `kubectl delete pod` — wait for Helm to finish first
- **Always commit before resuming** — FluxCD will overwrite local-only changes on its next reconcile (default: 5 minutes)
- **Namespace flag**: flux CLI uses `-n <namespace>`, not `--namespace`
- **Reconcile order when pushing**: source git → kustomization → helmrelease (only if needed)
