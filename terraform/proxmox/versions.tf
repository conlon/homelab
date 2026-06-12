terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.66"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.0"
    }
  }
}
