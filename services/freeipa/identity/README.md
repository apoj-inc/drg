# FreeIPA identity IaC

These files are the desired declarations for FreeIPA objects. Ansible applies
them with the `freeipa.ansible_freeipa` collection; it does not generate
objects or policies interactively.

## Files

- `users.yaml` — human users;
- `service_users.yaml` — non-interactive bind/service accounts;
- `groups.yaml` — groups and descriptions;
- `roles.yaml` — delegated FreeIPA roles and their members/privileges;
- `hosts.yaml` — host entries;
- `services.yaml` — Kerberos service principals;
- `hbac.yaml` — HBAC rules;
- `sudo.yaml` — sudo rules.

Passwords and key material must not be committed. A service user references
the name of an environment variable with `password_env`; the CI pipeline
populates that variable from OpenBao. For example:

```yaml
users:
  - name: app-bind
    state: present
    givenname: App
    sn: Bind
    password_env: APP_FREEIPA_BIND_PASSWORD
    password_expiration: 20380119031407Z
```

The corresponding field must be added to `services/freeipa/config.yaml` under
`secrets.fields`:

```yaml
secrets:
  path: kv/services/freeipa
  fields:
    - app_freeipa_bind_password
```

The pipeline then reads that field from OpenBao and exports it as
`APP_FREEIPA_BIND_PASSWORD`.

## Apply

The regular FreeIPA playbook applies all declarations on the primary replica
and runs the health and external-DNS acceptance checks on each replica:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/freeipa/ansible/freeipa.yml
```

To reconcile identity declarations only:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/freeipa/ansible/freeipa-objects.yml
```

An object is not removed merely because it is absent from a file. Deletion
must be explicit:

```yaml
users:
  - name: old-bind
    state: absent
```

Before an explicit deletion the role runs an online FreeIPA backup. Review the
change and backup result before applying it in production.

## Client enrollment

`managed-linux` is the host group for guests created by this repository.
Terraform uses the `ipa-enroller` password only during a resource create,
issues an OTP, joins the guest, and removes the bootstrap SSH key. Normal
service playbooks authenticate as `ansible-ci` with its FreeIPA password.

Every guest also enters `service-<service-name>` (for example,
`service-postgresql`) so future HBAC and sudo policies can target one service
without affecting the rest. A service may add more groups with
`freeipa_client.hostgroups` in its `config.yaml`; declare those groups in
`hostgroups.yaml` before deploying the service.

The two service-user passwords are supplied to CI from OpenBao. New guests are
enrolled only by the Terraform create pipeline; there is no separate migration
job.
Terraform deliberately preserves host entries on destroy. To retire one,
run `shared/terraform/scripts/deprovision_freeipa_host.py <host-fqdn>` with an
approved deletion keytab and `IPA_DEPROVISION_CONFIRM` set to that same FQDN.

## PKI

Custom Dogtag profiles may be placed in `pki/profiles/*.cfg`. Ansible imports
them only when absent; built-in FreeIPA/Dogtag profiles are not modified or
deleted.
