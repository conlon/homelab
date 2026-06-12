# ---------------------------------------------------------------------------
# Proxmox VM fleet — Pattern A (cloud image + cloud-init), per-node OS release.
#
# Each VM is built directly from an Ubuntu cloud image plus a cloud-init drive
# (hostname = map key, static IP, SSH key). No interactive installer, no golden
# template — bump a node's `release` to migrate just that host (see README).
# ---------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

locals {
  secrets = data.sops_file.secrets.data

  datastore  = "local-lvm"
  gateway    = "192.168.86.1"
  ci_user    = "michael"
  ssh_pubkey = pathexpand("~/.ssh/id_ed25519.pub") # TODO: confirm key path

  # Base images — one download per release that is actually referenced below.
  # To add 26.04: uncomment and paste the real codename/URL once it ships.
  releases = {
    noble = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img" # 24.04 LTS
    # "2604" = "https://cloud-images.ubuntu.com/<codename>/current/<codename>-server-cloudimg-amd64.img" # 26.04 LTS
  }

  # The fleet. hostname == map key.
  #   - add a host:     add an entry, `./run.sh apply`
  #   - migrate a host: change its `release`, `./run.sh plan` (verify!), apply
  # pve_node: which Proxmox host to place the VM on.
  # cores/memory: null falls back to defaults (4 cores, 4096 MB).
  nodes = {
    kami  = { vmid = 102, ip = "192.168.86.8",  release = "noble", pve_node = "prox0", cores = null, memory = null }
    kyoko = { vmid = 103, ip = "192.168.86.9",  release = "noble", pve_node = "prox0", cores = null, memory = null }
    n3    = { vmid = 104, ip = "192.168.86.29", release = "noble", pve_node = "prox1", cores = 2,    memory = 4096 }
  }

  # One image download per (pve_node, release) pair — keyed as "prox0:noble" etc.
  used_node_releases = {
    for k, n in local.nodes : "${n.pve_node}:${n.release}" => {
      pve_node = n.pve_node
      release  = n.release
    }
  }
}

provider "proxmox" {
  endpoint  = local.secrets["proxmox_endpoint"]  # e.g. https://192.168.86.4:8006/
  api_token = local.secrets["proxmox_api_token"] # root@pam!terraform=<uuid>
  insecure  = true                               # self-signed PVE cert

  # bpg needs SSH to the node for disk import / file ops.
  ssh {
    agent    = true
    username = "root"
  }
}

# ---------------------------------------------------------------------------
# Base images — downloaded once per referenced release, cached on the node.
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_download_file" "ubuntu" {
  for_each = local.used_node_releases

  content_type = "import"
  datastore_id = "local"
  node_name    = each.value.pve_node
  url          = local.releases[each.value.release]
  file_name    = "ubuntu-${each.value.release}-cloudimg.img"
}

# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_vm" "node" {
  for_each = local.nodes

  name          = each.key # -> guest hostname via cloud-init
  vm_id         = each.value.vmid
  node_name     = each.value.pve_node
  machine       = "q35"
  bios          = "ovmf"
  scsi_hardware = "virtio-scsi-single"
  on_boot       = true

  agent { enabled = true }

  cpu {
    type  = "x86-64-v2-AES" # vendor-neutral — stable on the AMD Ryzen host
    cores = coalesce(each.value.cores, 4)
  }

  memory {
    dedicated = coalesce(each.value.memory, 4096)
  }

  efi_disk {
    datastore_id      = local.datastore
    type              = "4m"
    pre_enrolled_keys = true
  }

  # --- OS / root disk: controlled by `release`, re-imaged on migration -------
  disk {
    datastore_id = local.datastore
    interface    = "scsi0"
    size         = 32
    iothread     = true
    ssd          = true
    discard      = "on"
    import_from  = proxmox_virtual_environment_download_file.ubuntu["${each.value.pve_node}:${each.value.release}"].id
  }

  # --- Persistent data disk: static, MUST survive OS migration ---------------
  # No image and no release input, so the only thing a migration changes is
  # scsi0's import source. Before applying any migration, confirm via
  # `terraform plan` that this disk is NOT in the destroy set (see README).
  disk {
    datastore_id = local.datastore
    interface    = "scsi1"
    size         = 100
    iothread     = true
    ssd          = true
    discard      = "on"
  }

  initialization {
    datastore_id = local.datastore

    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = local.gateway
      }
    }

    user_account {
      username = local.ci_user
      keys     = [trimspace(file(local.ssh_pubkey))]
    }
  }

  network_device {
    bridge   = "vmbr0"
    firewall = true
  }
}
