# OpenBao

OpenBao is deployed as an independent LXC and Docker Compose application. The
initial rollout keeps the existing service configuration intact: it uses the
legacy `vmbr1` address `192.0.2.26` and does not change any existing service
credentials or secret consumers. The optional `openbao_sdn_address` is reserved
for the later network migration.

OpenBao uses the shared PostgreSQL server for durable storage. Before deploying
OpenBao, run the PostgreSQL playbook once with `OPENBAO_POSTGRESQL_PASSWORD`
available; it creates the `openbao` database and role. The OpenBao deployment
requires the same password. Initialize and unseal it manually after the service
is healthy; do not put unseal keys or the initial root token in GitLab CI
variables or this repo.

```sh
cd services/openbao/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars

ansible-playbook -i 'openbao,' services/openbao/ansible/openbao.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/openbao.yml \
  -e "ansible_host=192.0.2.26"
```

The public endpoint is `https://openbao.example.test` after the managed Traefik
route has been applied. GitLab uses its declarative OIDC integration.
Woodpecker uses the separate internal mTLS listener on port `8202`, because
the public Traefik route terminates TLS and cannot forward a client
certificate to OpenBao's cert auth method.

## Declarative access configuration

Repository-owned OpenBao mounts, auth methods, roles, and policies live in
[`openbao-config/`](openbao-config/). They are YAML declarations; the Ansible
role renders ACL rules to the HCL required by the OpenBao API. Do not put
tokens, passwords, or private keys in this directory.

The playbook applies that configuration after the API health check. It accepts
`OPENBAO_CONFIG_TOKEN`. For initial bootstrap only, set
`OPENBAO_CONFIG_BOOTSTRAP=true` to allow `OPENBAO_ROOT_TOKEN` to be used.
Set `OPENBAO_CONFIG_APPLY=false` to inspect the declared files without writing
to OpenBao.

The declared SSH CAs and FreeIPA certificate auth method prepare the server
side. The `woodpecker-config-extension` certificate role is restricted to the
Woodpecker host DNS names and grants only the read-only `ci` policy. The
Woodpecker Ansible role enrolls the corresponding renewable client certificate
with certmonger; no OpenBao Agent or static CI token is installed. OpenBao's
server certificate for the mTLS listener is also enrolled by certmonger.

## PostgreSQL storage

Set `OPENBAO_POSTGRESQL_PASSWORD` as a masked, protected GitLab CI/CD variable.
Its environment scope must cover both `Pushkin/postgresql` and `Pushkin/openbao`
(for example, `Pushkin/*`). It is used by both Ansible jobs, so it must have the
same value in each environment. The following non-secret variables have safe
defaults, but set them in CI/CD if the PostgreSQL endpoint differs:

```text
OPENBAO_POSTGRESQL_HOST=postgresql.example.test
OPENBAO_POSTGRESQL_PORT=5432
OPENBAO_POSTGRESQL_DATABASE=openbao
OPENBAO_POSTGRESQL_USERNAME=openbao
OPENBAO_POSTGRESQL_SSLMODE=disable
OPENBAO_POSTGRESQL_TABLE=openbao_kv_store
OPENBAO_POSTGRESQL_HA_ENABLED=true
```

For a previously initialized file-backed instance, do not simply redeploy: the
data must first be migrated with OpenBao's storage migration procedure while
the service is stopped. Take a backup of the existing Docker volume and of the
PostgreSQL database before changing the backend. A new, uninitialized instance
can be deployed directly.

## OIDC providers

OIDC providers are declared in
[`openbao-config/auth/oidc.yaml`](openbao-config/auth/oidc.yaml). The
configuration currently includes both Keycloak and Authentik; each maps group
membership to OpenBao roles:

| Authentik group | OpenBao role | Policy |
| --- | --- | --- |
| `openbao-user` | `user` | read/list |
| `openbao-admin` | `admin` | full administration |

The groups must be assigned to users in the selected identity provider. The
protected environment must contain `OPENBAO_OIDC_CLIENT_SECRET` and
`KEYCLOAK_OPENBAO_CLIENT_SECRET`. Client secrets are referenced by environment
variable name and are not stored in the repository.

OIDC configuration is applied through the Vault-compatible API using the
`community.hashi_vault` collection. The GitLab Runner installs the collection
and the `hvac` Python dependency; the runner must be able to reach the OpenBao
API address.

The Forgejo Actions JWT auth definition is intentionally excluded from this
playbook because OpenBao must reach a running Forgejo discovery endpoint to
validate it. Run the Forgejo Ansible playbook with
`FORGEJO_OPENBAO_CONFIGURE_ACTIONS_AUTH=true` to apply that definition; the
Forgejo play delegates the OpenBao API calls to an OpenBao host.

CI reconciles the complete declarative configuration on every OpenBao
deployment. It uses `OPENBAO_CONFIG_TOKEN`. `OPENBAO_ROOT_TOKEN` is accepted only when
`OPENBAO_CONFIG_BOOTSTRAP=true`, and must not be committed or stored in the
repository.

## Prometheus metrics

Prometheus scrapes `GET /v1/sys/metrics?format=prometheus` without a token.
OpenBao enables this endpoint explicitly on its TCP listener; it is reachable
only from the application network.
