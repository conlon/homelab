# Proxmox operations

## Replace faulty disks on NAS
### Setup
Guide: https://frankschmidt-bruecken.com/en/blog/replace-defective-hard-disc-virtualised-truenas-on-proxmox/
1. Offline the faulty disk
2. Shutdown TrueNAS, then Proxmox
3. Replace disk(s), noting Serial Numbers
4. Boot Proxmox
5. Add new disks (below)

## Add new disks
### Step 1: Identify the New Hardware (In Proxmox)
Find the disk on the host that isn't already assigned to the VM by diffing the host disk list against `qm config`:

```bash
VM_ID=100

# ALL_DISKS — every ata- disk on the host
# VM_DISKS — disks already in the VM's config (extracted via grep -o 'ata-[^,]*')
# DISK_ID - 'comm -23' lines only in the first set = unassigned disk(s)
# SERIAL — extracted from the tail of DISK_ID automatically
# SCSI_SLOT: Generates scsi1–scsi8, removes any already in the config, and takes the first remaining. With example config scsi1–scsi3 used, that yields scsi4. Works correctly because all slots are single digits so lexicographic and numeric sort agree.
ALL_DISKS=$(ls /dev/disk/by-id/ | grep "^ata-" | grep -v "part")
VM_DISKS=$(qm config $VM_ID | grep -o 'ata-[^,]*')
DISK_ID=$(comm -23 <(echo "$ALL_DISKS" | sort) <(echo "$VM_DISKS" | sort))
SERIAL="${DISK_ID##*_}"

SCSI_SLOT=$(comm -23 <(seq 1 8 | sed 's/^/scsi/' | sort) <(qm config $VM_ID | grep -o 'scsi[0-9][0-9]*' | sort) | head -1)

echo "New disk: $DISK_ID  serial: $SERIAL  slot: $SCSI_SLOT"
```

If more than one disk is unassigned, `DISK_ID` will contain multiple lines — set it manually in that case.

### Step 2: Map the Drive to the VM (In Proxmox)

```bash
qm set $VM_ID -${SCSI_SLOT} /dev/disk/by-id/${DISK_ID},backup=0,serial=${SERIAL}
```

### Step 3: Clear the Fault and Replace (In TrueNAS)
Once the VM is started, verify TrueNAS sees the new drive:

```bash
ls /dev/sd*
```

Then use the GUI to replace the disk in the pool:

Storage > Pools > Status > find the OFFLINE or GUID entry > Replace > select the new drive.
