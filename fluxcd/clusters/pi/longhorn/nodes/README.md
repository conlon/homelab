# Longhorn Node Configurations

This directory contains Longhorn Node resources for VM hosts (joi, n0, n1, n2) that use dedicated storage disks.

## Configuration

Each VM host is configured to use `/mnt/longhorn` as the default disk path instead of `/var/lib/longhorn/` (which is on the root disk). This prevents disk pressure on the root filesystem.

## Setup Process

1. **Ansible Disk Setup**: All VM hosts in the `longhorn` group (defined in `ansible/host-config/inventory/hosts.yml`) automatically get their disks configured via the `storage` role:
   - Creates `/mnt/longhorn` directory
   - Partitions `/dev/sdb` (creates `/dev/sdb1`)
   - Formats `/dev/sdb1` with ext4
   - Mounts to `/mnt/longhorn` and adds to `/etc/fstab`

2. **Longhorn Node Resources**: These Kubernetes resources configure Longhorn to use the mounted disk at `/mnt/longhorn` instead of the default `/var/lib/longhorn/` path.

## Adding a New VM Host

To add a new VM host:

1. Add the host to the `longhorn` group in `ansible/host-config/inventory/hosts.yml`
2. Create a new node YAML file in this directory (e.g., `newhost-node.yaml`)
3. Add the resource to `kustomization.yaml` in this directory
4. Run the Ansible playbook to set up the disk: `ansible-playbook -i inventory/hosts.yml playbook.yml --tags storage`
5. Flux will automatically apply the Longhorn Node resource

