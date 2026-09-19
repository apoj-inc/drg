# Woodpecker CI

Woodpecker uses Forgejo as its OAuth provider. The value of
`woodpecker.forgejo_client` must be the client ID of a registered Forgejo OAuth2
application, and `WOODPECKER_FORGEJO_SECRET` must contain that application's
secret from OpenBao.

## Forgejo OAuth application

Create a system-wide OAuth2 application in Forgejo under:

```text
Site administration -> Applications -> OAuth2 Applications
```

Use this exact authorization callback URL:

```text
https://ci.example.test/authorize
```

Then set the generated client ID in `config.yaml`:

```yaml
woodpecker:
  forgejo_client: <generated Forgejo client ID>
```

Store the generated client secret in OpenBao at
`kv/services/woodpecker` as `woodpecker_forgejo_secret`. Do not use the
application display name as the client ID unless Forgejo actually generated
that value.

The current `Authorization failed / Client ID not registered` page means this
registration is missing or `woodpecker.forgejo_client` does not match the
registered application. After updating the client ID and secret, redeploy the
Woodpecker server and retry login.

## OpenBao CI authentication

The generated plan workflows use the Woodpecker secret name
`openbao_plan_token`; deployment workflows use `openbao_deploy_token`. The
appropriate secret is supplied automatically by the configuration
extension: the extension authenticates to OpenBao with a renewable FreeIPA
client certificate and the dedicated `woodpecker-config-extension` cert-auth
role. OpenBao issues a short-lived token with the read-only `ci` policy for
`kv/data/example-cluster` and `kv/data/services/*`.

The certificate is enrolled and renewed by Ansible/certmonger on the
Woodpecker host. The extension connects to the internal OpenBao mTLS listener,
not the public Traefik URL, and only enables this secret for the allow-listed
`example-org/drg` repository. No OpenBao token should be stored in Woodpecker or in
the repository.

## Activate the repository webhook

After the OAuth application is configured, sign in to Woodpecker and activate
the repository:

```text
https://ci.example.test/repo/add
```

Select `example-org/drg` and enable it. Woodpecker then creates the Forgejo repository
webhook with a signed URL based on `WOODPECKER_EXPERT_WEBHOOK_HOST`; the hook
endpoint is `/api/hook`. Do not create a bare Forgejo webhook pointing at
`/api/hook`, because it has no repository access token and Woodpecker will
reject it.

The repository's `.woodpecker/validate.yml` runs on pull requests and on pushes
to `drunk`, so subsequent commits to `drunk` start the validation and rollout
planning pipeline automatically. Enable `Allow deployments` in the Woodpecker
repository settings to expose the native **Deploy** action after a successful
plan. Protect `drunk` in Forgejo so only Maintainers/Administrators can push or
merge to it.

Use the Deploy form with one target in the exact format
`service:cluster:contour`, for example:

```text
woodpecker:example-cluster:a
```

The deployment event is accepted only on `drunk`, must refer to a service
affected by the successful plan commit, and runs only that service, cluster,
and contour. The deployment pipeline performs plan and apply for the selected
target, followed by HA-sync only when that target is the service's final
contour, then Traefik and DNS reconciliation when required.

The Woodpecker server is deployed active-passive. All candidates share the
PostgreSQL database, but only the etcd lease holder runs the server because the
workflow queue is process-local. The separate `woodpecker_agent` service still
provides parallel execution capacity. The number of candidates is not limited
to two; etcd elects one owner from the service inventory group.

The shared `etcd_ha` role installs the `woodpecker-etcd-ha.service` controller
and the `woodpecker-ha.service` application unit. It uses the existing etcd
quorum with a service-specific key, refreshes ownership, and stops the server
when ownership or health is lost. The Woodpecker HA role retains the signing
key-aware control command and configuration-extension lifecycle.

The legacy two-node Corosync/Pacemaker flow remains available by setting
`woodpecker_ha_backend: pacemaker` and supplying the old Corosync key.

```bash
# Run on each candidate while the old, directly-managed server is still present.
sudo systemctl disable --now woodpecker-config-extension || true
sudo docker compose -f /opt/woodpecker/docker-compose.yml stop woodpecker-server

# Start the etcd controller after all candidate hosts are configured.
sudo systemctl enable --now woodpecker-etcd-ha.service
```

The controller is intentionally fail-closed when etcd is unavailable. It does
not perform automatic fencing, so an operator must fence or verify a stale
candidate before a manual recovery if the controller itself is no longer
running. Since Woodpecker state is in PostgreSQL and the
configuration-extension credentials are provisioned on each VM, no DRBD or
shared filesystem is required for this service.

Rollout workflows are generated from the changed-file list supplied by
Woodpecker. A change under `services/<name>/` selects that service only;
changes under `shared/`, `ci/`, `environments/`, or `services/freeipa/` select
all services because they can affect the whole platform. Deployment events do
not carry changed-file metadata, so the configuration extension derives the
list from the exact deployment commit and fails closed if it cannot do so.
