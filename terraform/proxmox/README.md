# Proxmox VM fleet (Terraform)

Declarative VM provisioning for prox0 using the [`bpg/proxmox`](https://registry.terraform.io/providers/bpg/proxmox/latest/docs)
provider. Each VM is built from an **Ubuntu cloud image + cloud-init** (Pattern
A) — no interactive installer. Cloud-init sets the hostname (= the map key), the
static IP, and your SSH key, so Ansible can connect on first boot.

## Why this exists

Bootstrapping used to mean clicking through the Ubuntu live installer for every
VM and hand-setting hostname/IP. Now:

```sh
# add `newhost = { vmid = 110, ip = "192.168.86.40", release = "noble" }` to
# local.nodes in main.tf, then:
./run.sh apply
```

The VM is created, cloud-init'd, and SSH-ready in ~20s.

## Prerequisites

1. **Proxmox API token** — create one for Terraform on prox0:
   ```sh
   pveum user token add root@pam terraform --privsep 0
   ```
   (or a dedicated user with VM.* + Datastore.* perms).
2. **SSH agent** — bpg SSHes to the node as `root` for disk import. Make sure
   `ssh root@192.168.86.4` works via your agent (`ssh-add`).
3. **`secrets.sops.yaml`** — sops-encrypted (same age key as the rest of the
   repo, see `terraform/.sops.yaml`). Required keys:
   ```yaml
   proxmox_endpoint: https://192.168.86.4:8006/
   proxmox_api_token: root@pam!terraform=<uuid-secret>
   r2_access_key_id: <for the state backend>
   r2_secret_access_key: <for the state backend>
   ```
   Create with: `sops secrets.sops.yaml` (the `.sops.yaml` rule encrypts it).
4. **Confirm the TODOs in `main.tf`**: `ssh_pubkey` path and the per-node IPs.

## Usage

```sh
./run.sh init      # first time / after backend changes
./run.sh plan
./run.sh apply
```

## Migrating a host one-by-one (e.g. 24.04 → 26.04)

This module pins the base image **per node**, and the **100 GB data disk
(`scsi1`) is static** so it can survive an OS rebuild. Procedure:

1. Add the new release to `local.releases` (real codename + URL).
2. Drain/cordon the node first if it runs k8s/Longhorn workloads.
3. Change *one* node's `release` in `local.nodes`.
4. **`./run.sh plan` — and read it carefully.** You want to see the root disk
   (`scsi0`) re-imaged and the VM updated, with **`scsi1` NOT in the destroy
   set** and **no other node touched**.
5. If the plan looks right, `./run.sh apply`. Then let Ansible reconfigure it.

> ⚠️ **The data-disk gate.** A Proxmox VM destroy removes its referenced disks.
> If the plan shows the **whole VM being replaced** (not just the scsi0 disk
> re-imaged), `scsi1` would be destroyed with it — **do not apply**. In that
> case either:
>   - do the OS upgrade **in place** via Ansible (`do-release-upgrade`) and keep
>     Terraform for create/scale only, or
>   - detach `scsi1` first (`qm set <id> --delete scsi1` leaves it as an unused
>     volume), apply the rebuild, then reattach it.
>
> The whole point of step 4 is to find out which case you're in *before*
> touching anything — `plan` changes nothing.

## Importing the existing hand-made VMs

`kami` (102) and `kyoko` (103) were created by hand (`qm create`), so Terraform
doesn't know about them yet. Before the first `apply`, either:

- **start clean** (recommended, they're empty): `qm destroy 102 --purge` /
  `qm destroy 103 --purge`, then `./run.sh apply` recreates them, **or**
- **import** them: `./run.sh import 'proxmox_virtual_environment_vm.node["kami"]' prox0/102`
  (and likewise for kyoko) — expect a non-trivial plan diff to reconcile.
