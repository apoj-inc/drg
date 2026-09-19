# PostgreSQL service

Three-node PostgreSQL cluster for GitLab, authentik, NetBox, NetBird, PowerDNS,
OpenBao, and Keycloak application databases.

PostgreSQL runs on three VM instances managed by Patroni. PgBouncer is co-located on every PostgreSQL VM with one primary-only listener:

- `6432`: read/write pooler, active only on the current Patroni primary

Traefik exposes the stable client endpoint:

- `postgresql.example.test:5432`: read and write traffic to the current primary

The other two PostgreSQL instances are hot standby failover backups. They do not receive application read traffic.

## Dependencies

Deploy [`services/etcd`](../etcd/README.md) first. Patroni uses the three-node etcd quorum as its external DCS.

Required secrets:

```powershell
$env:TF_VAR_postgresql_admin_password = "<secret>"
$env:TF_VAR_postgresql_replication_password = "<secret>"
$env:TF_VAR_postgresql_gitlab_password = "<secret>"
$env:TF_VAR_postgresql_gitlab_registry_password = "<secret>"
$env:TF_VAR_postgresql_authentik_password = "<secret>"
$env:TF_VAR_postgresql_netbox_password = "<secret>"
$env:TF_VAR_postgresql_netbird_password = "<secret>"
$env:TF_VAR_postgresql_powerdns_password = "<secret>"
$env:OPENBAO_POSTGRESQL_PASSWORD = "<secret>"
```

## Terraform / OpenTofu

The service uses `services/postgresql/config.yaml` with the shared `service_config` module. Each PostgreSQL VM has a 10 GiB boot disk and a separate 120 GiB `scsi1` data disk. Ansible formats and mounts the data disk at `/var/lib/postgresql` before PostgreSQL or Patroni starts. Contour `A` provisions two PostgreSQL instances:

- `postgresql-node-a`, VM ID `1210`
- `postgresql-example-cluster-a-02`, VM ID `1211`

```sh
cd services/postgresql/terraform
tofu init
tofu plan
tofu apply
```

## Ansible

Run against the NetBox dynamic inventory:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/postgresql/ansible/postgresql.yml \
  --limit 'postgresql-example-cluster-a-*'
```

Useful overrides:

PgBouncer settings, including pool capacity, are declared in
`services/postgresql/config.yaml` under `pgbouncer`.

The role creates these databases:

- `gitlabhq_production` owned by `gitlab`
- `registry` owned by `registry`
- `authentik` owned by `authentik`
- `netbox` owned by `netbox`
- `netbird` owned by `netbird`
- `powerdns` owned by `powerdns`
- `openbao` owned by `openbao`
