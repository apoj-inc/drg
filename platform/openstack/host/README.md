# OpenStack host preflight

This directory contains the host-side preparation contract for the single
OpenStack node `192.0.2.2`. It deliberately does not repartition disks or
create filesystems automatically.

Before running Kolla-Ansible, collect the read-only audit:

```bash
SSH_KEY=~/.ssh/id_ed25519_sk_nontouch \
  ./platform/openstack/scripts/audit-host.sh 192.0.2.2
```

The output must be reviewed and recorded in `MIGRATION_LOG.md`. In particular,
the exact unused block device for Cinder, the management NIC, provider/veth
design, gateway, DNS, MTU, CPU and RAM must be confirmed before host bootstrap.

The legacy Proxmox runtime is outside this host bootstrap. It remains the
rollback environment while services are migrated manually.

## Bootstrap playbook

Run from the repository root on the local workstation:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local-temp \
ansible-playbook \
  -i platform/openstack/kolla/inventory/example \
  platform/openstack/host/ansible/host-bootstrap.yml \
  --limit openstack_hosts \
  -e ansible_user=root \
  -e ansible_ssh_private_key_file=$HOME/.ssh/id_ed25519_sk_nontouch
```

The playbook does not run `parted`, `mkfs`, `pvcreate`, `vgcreate` or any
other storage initialization. It fails if the host identity, management bridge,
KVM device or root free-space gate is wrong.
