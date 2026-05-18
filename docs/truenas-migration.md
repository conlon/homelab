# TrueNAS Migration Plan

Migrate all homelab services from the WD PR4100 (192.168.86.10) to TrueNAS (192.168.86.11).
After migration the PR4100 stays on the network but no cluster services depend on it.

## State

- Media files: **already on TrueNAS** at `/mnt/nas/media`
- TrueNAS NFS path for media: `192.168.86.11:/mnt/nas/media`
- PR4100 NFS path (current): `192.168.86.10:/nfs/media`
- TrueNAS is already in use for sync (`/mnt/nas/sync`) and stash (`/mnt/nas/cloud`)

## Open Questions (resolve before executing)

- [x] **Plex current URL**: Was on PR4100 — fully migrated to TrueNAS native app.
- [x] **Plex new URL**: Configured and working.
- [x] **Plex metadata**: Migrated from PR4100 to TrueNAS.
- [x] **NFS export confirmed**: `192.168.86.11:/mnt/nas/media` is exported and accessible from cluster nodes.
- [ ] **Sync deployment fate**: The `sync` deployment still mounts `/nfs/michael/sync` from the PR4100. The `sync-truenas` deployment already covers this from TrueNAS. Should `sync` be removed after migration?

---

## Phase 1 — Set Up Plex on TrueNAS ✅

- [x] Install Plex Media Server via TrueNAS App Catalog
- [x] Migrate Plex metadata/database from PR4100 to TrueNAS
- [x] Verify Plex starts correctly, libraries are intact, and playback works
- [x] Set up ingress/DNS for new Plex instance
- [x] Update **Overseerr** (`home/overseerr/` and `home/overseerr-le/`) to point to new Plex URL
- [x] Update **Overseerr-priv** (`fluxcd-priv/home/overseerr/`) to point to new Plex URL

---

## Phase 2 — Update NFS Mounts (homelab)

All of the following change `server: 192.168.86.10` / `path: /nfs/media` →
`server: 192.168.86.11` / `path: /mnt/nas/media`.

### StatefulSets (plain manifests — apply with `kubectl apply -k`)

| App | File |
|-----|------|
| sonarr | `fluxcd/clusters/pi/home/sonarr/statefulset.yaml` |
| sonarr-4k | `fluxcd/clusters/pi/home/sonarr-4k/statefulset.yaml` |
| radarr | `fluxcd/clusters/pi/home/radarr/statefulset.yaml` |
| radarr-4k | `fluxcd/clusters/pi/home/radarr-4k/statefulset.yaml` |
| radarr-le | `fluxcd/clusters/pi/home/radarr-le/statefulset.yaml` |
| bazarr | `fluxcd/clusters/pi/home/bazarr/statefulset.yaml` |
| sabnzbd | `fluxcd/clusters/pi/home/sabnzbd/statefulset.yaml` |

### HelmRelease (apply with k8s-apply skill)

| App | File |
|-----|------|
| qbit | `fluxcd/clusters/pi/home/qbit/helm.yaml` → `persistence.media.server` |

### Steps for each app

1. `flux suspend helmrelease <name> -n home` (or suspend kustomization for plain manifests)
2. Edit the manifest
3. `kubectl apply -k fluxcd/clusters/pi/home/<app>/`
4. Verify pod comes up and can read `/media`
5. Commit + push

- [x] sonarr
- [x] sonarr-4k
- [x] radarr
- [x] radarr-4k
- [x] radarr-le
- [x] bazarr
- [x] sabnzbd
- [x] qbit

> **Note:** Each app also has an old `deployment.yaml` still referencing `192.168.86.10`. Those Deployments exist in the cluster at 0 replicas and are inactive — superseded by the StatefulSets. They can be cleaned up in Phase 5.

> **TrueNAS ZFS fix applied:** `zfs set aclmode=passthrough nas/media` — required for NFS clients to `chmod`/`utimes` files they own (was `restricted`, which blocked POSIX permission calls even for the file owner, breaking 7za unpack).

---

## Phase 3 — Update NFS Mounts (fluxcd-priv) ✅

| App | File |
|-----|------|
| sonarr-priv | `clusters/pi/home/sonarr/statefulset.yaml` |
| radarr-priv | `clusters/pi/home/radarr/statefulset.yaml` |

- [x] sonarr-priv
- [x] radarr-priv

---

## Phase 4 — Update DNS / Proxy References

### homelab — `nas` external-dns (currently proxies PR4100 web UI)

`fluxcd/clusters/pi/home/1-external-dns/nas/`

- [ ] Decide: retire `nas.local.fellowfreak.dev` entirely, or re-point it to the TrueNAS web UI (192.168.86.11)?
- [ ] Update/remove `endpoints.yaml` (currently hardcodes `192.168.86.10`)
- [ ] Update/remove `middleware.yaml` (currently sets `Host: 192.168.86.10`)
- [ ] If keeping: update IP to `192.168.86.11` and rename hostname to `nas.local.fellowfreak.dev` → TrueNAS UI (already has its own `truenas.local.fellowfreak.dev`)

### fluxcd-priv — `wdnas` external-dns

`clusters/pi/home/external-dns/wdnas/`

- [ ] `service.yaml` currently has `externalName: 192.168.86.10` — remove or re-point
- [ ] If no longer needed, remove the `wdnas` kustomization entry from `external-dns/kustomization.yaml`

---

## Phase 5 — Clean Up PR4100 References

> **qBit seeding note**: After migration, qBit showed 1252 errored torrents because `downloads/` was never synced from PR4100. The media files are on TrueNAS but the downloads folder (which was hardlinked to media on PR4100) was not. Chose Option B (clear errors) for now. Consider Option A later if seeding ratios matter: rsync `downloads/` from PR4100 → `/mnt/nas/media/downloads/`, then run `jdupes -rL /mnt/nas/media/` to re-hardlink duplicates and recover the space.

- [ ] Remove/deprecate the `sync` deployment (`fluxcd/clusters/pi/home/sync/`) — `sync-truenas` already covers this
  - Confirm `sync-truenas` is healthy and in sync first
- [ ] Remove stale `deployment.yaml` files for sonarr, sonarr-4k, radarr, radarr-4k, radarr-le, bazarr — all still reference `192.168.86.10` but are 0-replica inactive; superseded by StatefulSets
- [ ] Verify no remaining `192.168.86.10` references in either repo:
  ```sh
  grep -r "192.168.86.10\|pr4100" ~/git/homelab/fluxcd/ ~/git/fluxcd-priv/ --include="*.yaml"
  ```
- [ ] Update any Uptime Kuma monitors that point at PR4100 services

---

## Phase 6 — Verify & Sign Off

- [ ] All *arr apps can scan/import media from new NFS path
- [ ] SABnzbd downloads land correctly (check incomplete/complete paths)
- [ ] qBit downloads land correctly
- [ ] Plex sees all libraries, playback works
- [ ] Overseerr requests flow through correctly
- [ ] No cluster pods show `CrashLoopBackOff` or mount errors
- [ ] Run: `grep -r "192.168.86.10" ~/git/homelab/fluxcd/ ~/git/fluxcd-priv/ --include="*.yaml"` → should be empty

---

## Future: NFS Permission Structure

**Current state:** Single NFS export (`192.168.86.11:/mnt/nas/media`) with `mapall_user = media`. Every pod on
every node appears as the same `media` user — no differentiation between apps.

**Risk:** All apps (SABnzbd, qBit, Sonarr, Radarr, Plex, Bazarr) have identical read/write access to all of
`/mnt/nas/media`. A misconfigured or compromised container could delete or overwrite anything.

**Recommended quick win:** Mount Plex's NFS volume as `readOnly: true` in its pod spec. Plex only needs to read
media, never write it. This prevents any Plex bug or exploit from corrupting the library with zero infrastructure
changes. Check `fluxcd/clusters/pi/home/` (or the TrueNAS native app config) for the Plex NFS mount.

**Longer-term option:** Enforce the natural data flow with per-role permissions:
- `downloads/` — read/write for SABnzbd, qBit, Sonarr, Radarr; no access for Plex
- `tv/`, `movies/`, `anime/`, etc. — read/write for Sonarr/Radarr (import); read-only for Plex, Bazarr
- `downloads/incomplete/` — write-only for SABnzbd/qBit

Implementing this properly requires either multiple NFS exports with different `mapall` users or NFSv4 with
per-UID ACLs and distinct container UIDs — meaningful complexity for a single-user homelab on a trusted private
network. Revisit if/when adding multi-user access or exposing services more broadly.
