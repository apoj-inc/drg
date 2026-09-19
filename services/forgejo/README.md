# Forgejo

Forgejo runs active-passive with ownership elected through the existing etcd
quorum. DRBD remains the default two-node storage backend and replicates one
filesystem mounted at `/srv/forgejo-drbd`; the filesystem contains `data/` and
`repositories/`. A future CephFS/NFS-style RW mount can be selected with
`storage.ha_backend: shared_filesystem` when three or more candidates are
available.

The DRBD backing device is the persistent udev path for the Terraform-attached
Proxmox `scsi1` disk. Do not change it to the OS disk (`/dev/sdb` on the
current guests).

Forgejo stores managed file storage in the `forgejo` S3 bucket through the
internal VersityGW endpoint. Relational data remains in the existing Patroni
PostgreSQL cluster. DRBD is not a backup and does not replace database, S3, or
repository backups.

## HA preparation

The shared `etcd_ha` role installs a lease controller and a disabled service
unit. The controller grants ownership to one candidate at a time, starts the
service only for the lease holder, and stops it when ownership or health is
lost. The legacy `pacemaker_ha` backend remains available by setting
`forgejo_ha_backend: pacemaker`.

With the default `storage.ha_backend: drbd`, run the playbook on both hosts
after Terraform has attached the second disk. Ansible installs and configures
DRBD but does not format the disk, force-promote DRBD, mount the filesystem, or
start Forgejo. The operator command remains available for the legacy
Pacemaker backend:

```text
/usr/local/sbin/forgejo-ha-resource-setup
```

Perform data migration manually before enabling the etcd-controlled service.
Copy repositories and the existing `/var/lib/forgejo` contents into the
replicated filesystem, preserving ownership and embedded SSH host keys. Audit
attachments, LFS, avatars, packages, and Actions data before removing old
storage; these normally belong in S3.

On DRBD, the first etcd owner must be the only node promoted. The controller
does not automatically fence a node that loses contact with etcd; manually
fence or verify the old primary before any migration or failover. Do not
promote a node while the former primary can still write to the DRBD device.
etcd is a coordinator, not a storage replication or fencing system.

For three or more Forgejo candidates, change `storage.ha_backend` to
`shared_filesystem` and provide a RW mount source through the HA variables.
The repository consumes that mount; provisioning CephFS is intentionally left
for a later infrastructure change.

## OpenBao Actions authentication bootstrap

The Forgejo Actions JWT auth method is configured from this playbook because
OpenBao must be able to reach Forgejo's OIDC discovery endpoint before it can
validate that configuration. The generic OpenBao playbook deliberately skips
`forgejo-actions.yaml`.

For the bootstrap run, enable the delegated task with
`FORGEJO_OPENBAO_CONFIGURE_ACTIONS_AUTH=true`. It uses
`OPENBAO_CONFIG_TOKEN`; during initial OpenBao bootstrap it uses
`OPENBAO_ROOT_TOKEN` when `OPENBAO_CONFIG_BOOTSTRAP=true`. The OpenBao API
operation is delegated to the first host in the `openbao` Ansible group, and
the task first waits for Forgejo to expose the configured OIDC discovery URL.
