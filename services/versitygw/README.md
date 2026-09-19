# VersityGW

VersityGW exposes a POSIX S3 endpoint through internal Traefik routing. Storage
is selected by `storage.mode` in `config.yaml`:

- `local_disk`: uses the VM's dedicated `/dev/sdb` data disk.
- `nfs`: uses the configured NFS export.

The current mode is `local_disk`. Both configurations remain present so the
service can be deliberately switched back to NFS if required.

The service must have only one active instance for a given S3 dataset. Separate
local disks on multiple replicas are independent stores and must not be placed
behind the same S3 endpoint without an explicit replication design.

## Migration from the old NFS store

Set `storage.mode: local_disk`, provision the new VM data disk with Terraform,
but do not run the VersityGW Ansible playbook until the copy is complete. The
local disk deployment formats `/dev/sdb` only when it has no filesystem, then
mounts it persistently; it does not copy or delete the old NFS data
automatically.

Example migration commands on the VersityGW VM:

```bash
docker compose -f /opt/versitygw/docker-compose.yml down
mkdir -p /mnt/versitygw-local
mkfs.ext4 /dev/sdb                 # only if /dev/sdb has no filesystem
mount /dev/sdb /mnt/versitygw-local
rsync -aHAX --numeric-ids /srv/versitygw-data/ /mnt/versitygw-local/
umount /mnt/versitygw-local
umount /srv/versitygw-data
# Remove the old NFS entry for /srv/versitygw-data from /etc/fstab.
```

Verify the copied data, remove the old NFS fstab entry, and then run the
Ansible deployment. The local-disk role refuses to hide an active NFS mount.
To switch back, set `storage.mode: nfs` and deploy again; the NFS settings are
kept in the service configuration.
