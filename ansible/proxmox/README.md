# Proxmox operations

## Replace faulty disks on NAS
Guide: https://frankschmidt-bruecken.com/en/blog/replace-defective-hard-disc-virtualised-truenas-on-proxmox/
1. Offline the faulty disk
2. Shutdown TrueNAS, then Proxmox
3. Replace disk(s), noting Serial Numbers
4. Boot Proxmox
5. Passthrough new disks to TrueNAS
  a. verify 