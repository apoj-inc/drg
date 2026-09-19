# Shared Terraform resources

This directory contains reusable Terraform modules and helper scripts used by service roots.

- `proxmox_lxc_container` creates a reusable Proxmox LXC container from an existing Proxmox template file and can allocate/register VM addresses through NetBox IPAM.
- `proxmox_vm_cloudinit_template` creates a Proxmox VM template from an explicitly specified Debian GenericCloud QCOW2 image.
- `proxmox_cloudinit_vm` creates a linked clone (`full = false`) of a cloud-init VM template and applies per-VM hostname, credentials, DNS, and network configuration.

For NetBox-managed interfaces, service roots pass `mode = "netbox"` with `prefix`, `vrf`, and `vnet`. The module reserves an address from NetBox before creating the LXC, discovers the Proxmox VNet bridge and gateway from NetBox metadata, then registers the VM, interfaces, all IP addresses, and primary IPv4 for Ansible dynamic inventory.

Cloud-init VM consumers must provide both `image_url` and `image_filename` explicitly. The VM template and every linked clone must be placed on storage that supports linked cloning.

Do not run Terraform from this directory directly. Run it from a service directory, for example:

```sh
cd services/gitlab/terraform
tofu init
tofu apply
```
## Service VM disk I/O limits

Service VM Terraform modules use 200 MB/s read and 100 MB/s write limits by
default. A service can override these values in its `config.yaml`:

```yaml
compute:
  io:
    read_mbps: 200
    write_mbps: 100
```

The settings apply to the VM boot disk managed by the shared VM module. LXC
containers do not currently support equivalent disk I/O fields in the
configured Proxmox Terraform provider.

VM services can optionally attach a separate data disk on `scsi1`. Set
`compute.data_disk_gb` in the service configuration; the disk uses the boot
disk datastore unless `compute.data_disk_datastore` is set. The shared module
leaves the disk unformatted so the service's configuration management can
choose the filesystem and mount point.

VM memory ballooning is enabled by setting the VM's minimum memory below its
dedicated maximum. If unspecified, the minimum equals `compute.memory_mb` and
memory remains fixed. Services can opt into reclaimable memory with
`compute.memory_min_mb`:

```yaml
compute:
  memory_mb: 8096
  memory_min_mb: 4096
```
