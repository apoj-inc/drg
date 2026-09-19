# FreeIPA

FreeIPA is deployed natively on Fedora Server VMs as a replicated IdM
installation. It provides LDAP/Kerberos identity services and a bind user for
Keycloak LDAP federation.

Required OpenBao fields at `kv/services/freeipa`:

```text
freeipa_admin_password
freeipa_directory_manager_password
keycloak_freeipa_bind_password
```

The default public host is `ipa.example.test`. Traefik publishes only the web
UI. LDAP, LDAPS, Kerberos, and password-change traffic uses the native replicas
directly through external DNS/SRV records.
Repository-owned FreeIPA policy and identity declarations are under
`pki/` and `identity/`. Usage is documented in
[`identity/README.md`](identity/README.md). Human users are declared in
`identity/users.yaml`; non-interactive service accounts, including
`keycloak-bind`, are declared separately in `identity/service_users.yaml`.

Полный справочник всех файлов и YAML-структур находится в
[`CONFIGURATION.md`](CONFIGURATION.md).

`ipa-healthcheck --failures-only` checks are scoped to FreeIPA service, CA,
replication, topology, and certificate sources. The `ipahealthcheck.ipa.idns`
source is intentionally not used as a failure gate because this deployment
uses external DNS (`setup_dns: false`). External A/SRV/TXT records are tested
separately.

The FreeIPA LDAP, LDAPS, Kerberos, and password-change ports are internal and
are reached directly on the native replicas. PowerDNS publishes the replica A
records and the FreeIPA LDAP/Kerberos SRV records used for service discovery.
Traefik may publish the web UI through a restricted HTTP route if required.

The VMs use the Fedora 44 cloud-init template from `services/vm_template`.
The first VM is installed with `ipa-server-install`; the second is installed
with `ipa-replica-install` and the integrated CA is enabled on both replicas.

The installer `--ip-address` value comes from NetBox inventory `ansible_host`;
ad-hoc runs must pass `-e ansible_host=<vm-ip>`. The generated inventory
hostname is used as the unique FreeIPA server FQDN; `ipa.example.test` remains
the service/UI alias.

`freeipa.reset_data` is intentionally rejected for native deployments. Removing
an IdM database is a destructive migration action and must be performed
manually with an explicit backup and recovery procedure.

## Cutover order

1. Create the Fedora template, then apply FreeIPA contours A and B. The first
   host creates the new CA/domain; the second joins it as a replica.
2. Run the FreeIPA Ansible playbook serially. Verify `ipa-healthcheck`,
   `ipa-replica-manage list`, and direct LDAP/Kerberos connectivity on both
   hosts.
3. Reconcile PowerDNS and verify `_ldap._tcp`, `_kerberos._tcp/_udp`,
   `_kerberos-master._tcp/_udp`, `_kpasswd._tcp/_udp`, and `_kerberos` TXT
   records. Remove old Docker FreeIPA SRV records only after this check.
4. Create/re-enroll users and service principals in the fresh domain. Password
   hashes from the old container are not imported.
5. Deploy Keycloak A/B, allow its initial LDAP full sync to finish, then set
   `keycloak.freeipa.full_sync_on_deploy` to `false` for normal idempotent
   deploys. Deploy OAuth2 Proxy A/B after Keycloak readiness is confirmed.
6. Validate the Traefik routes and OAuth callback flow, then stop and retain
   the old FreeIPA container for rollback evidence until the migration window
   is closed.
