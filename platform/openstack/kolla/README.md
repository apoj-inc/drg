# Kolla-Ansible deployment

Use a pinned Python virtual environment for Kolla-Ansible 2026.1. Do not
commit `/etc/kolla/passwords.yml`, rendered `clouds.yaml`, tokens or private
keys.

Kolla passwords are supplied by OpenBao as the base64-encoded file secret
`kv/platform/openstack:kolla_passwords`. The pipeline materializes it only as
a temporary mode-0600 file and passes that file to Kolla-Ansible for both
`prechecks` and `deploy`. Do not run `kolla-genpwd` in CI and do not commit the
decoded file.

Required sequence:

```text
bootstrap-servers -> prechecks -> pull -> deploy -> post-deploy
```

Run `prechecks` only after copying `globals.yml.example` to a private
`globals.yml` and replacing every `REPLACE_WITH_*` value with values recorded by host audit and approved network/storage
reservations. `init-runonce` is not part of this production deployment.

The `example` inventory intentionally assigns all initial roles to
`openstack-node-01` (`192.0.2.2`). This is a single failure domain, not HA.

## Dashboards and internal SSO

The initial deployment enables both dashboards:

- Horizon: `horizon.example.test`;
- Skyline: `skyline.example.test`.

These are internal test endpoints. Do not create public DNS records or change
CI. The test workstation must resolve them to the approved Kolla VIP.

Authentik is configured as the Keystone OpenID federation provider. The
`openstack-keystone` client redirect URI is the mod_auth_openidc callback:

```text
https://openstack.example.test/redirect_uri
```

The legacy federation endpoint URI is retained in Authentik for rollback. The
client remains confidential and uses the authorization-code flow; Kolla is
configured with `keystone_federation_oidc_response_type: "code"`.

Store the client secret in OpenBao and render Keystone federation metadata and
mapping files into `/etc/kolla` during the local Ansible/Kolla playbook run.
Never commit the secret. Before enabling SSO, verify internal DNS and a
certificate trusted by the test workstation.
