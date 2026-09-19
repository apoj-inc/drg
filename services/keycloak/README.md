# Keycloak

Keycloak is deployed as two Docker Compose replicas in separate Proxmox LXC
containers. Both use the shared PostgreSQL database and the embedded
`jdbc-ping` cache stack for cluster discovery. Traefik routes only to healthy
replicas using `/health/ready` on the management port.

During the first rollout contour, Ansible uses `/health/live` to allow the
second contour to be deployed and join the cluster. This does not change the
Traefik health check: traffic is still sent only to replicas that pass
`/health/ready`.

Required OpenBao fields at `kv/services/keycloak`:

```text
keycloak_admin_password
keycloak_postgresql_password
keycloak_freeipa_bind_password
```

The default public host is `keycloak.example.test`; the realm issuer URL is:

```text
https://keycloak.example.test/realms/drg
```

The FreeIPA bind account is expected at
`uid=keycloak-bind,cn=users,cn=accounts,dc=example,dc=test`.

The `drg` realm also defines parallel confidential OIDC clients for OAuth2
Proxy, GitLab, NetBox, and OpenBao. Their secrets are stored under
`kv/services/keycloak` and injected as `KEYCLOAK_*_CLIENT_SECRET` variables.

User federation reconciliation is configured under
`keycloak.freeipa.federation_reconciliation`. With
`remove_unmanaged: true`, the deployment reads all LDAP user-storage
providers in the application and `master` realms and removes providers whose
names are not in the configured `application_providers` or `master_providers`
lists. Add any intentionally retained external provider to those lists before
enabling this option.

The deployment also federates FreeIPA into the Keycloak `master` realm when
`keycloak.freeipa.master_admins.enabled` is true. The provider is named
`freeipa-master-admin` by default. The FreeIPA `admins` group is mapped to a
Keycloak group and receives the master-realm `admin` role. This is a global
Keycloak superuser mapping; use a dedicated FreeIPA group instead of `admins`
if narrower administrative access is required.

FreeIPA is configured as one LDAP federation provider with both native LDAPS
replica URLs. Keycloak tries those URLs sequentially when the first replica is
unavailable; they must remain replicas of the same LDAP directory. The
FreeIPA CA is installed into the Keycloak truststore. Kerberos integration is
intentionally disabled: Keycloak authenticates users through LDAP.

User passwords are not imported into Keycloak. On a fresh FreeIPA domain,
users must be created or re-enrolled in FreeIPA and Keycloak's initial full
LDAP sync must complete before application cutover. The migration config leaves
`full_sync_on_deploy` enabled for the first cutover; disable it afterward to
avoid an unnecessary full sync on every routine deploy.

Authentik remains the default provider for existing routes and applications.
