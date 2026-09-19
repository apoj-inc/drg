# Declarative OpenBao configuration

This directory is the repository-owned source of truth for OpenBao access
configuration. Files contain only public parameters, policy rules, and names of
environment variables; do not put tokens, passwords, private keys, or
certificates with private keys here.

`secrets-engines/` declares mounts and their roles. `auth/` declares auth
methods and their roles. `policies/` declares ACL rules in YAML; the Ansible
role renders them to the HCL accepted by the OpenBao API. `roles/` is an
operator-facing index of the important roles and their purpose.

The FreeIPA CA file referenced by `auth/cert.yaml` is public trust material.
The `ipa-host-bootstrap` role intentionally cannot read KV secrets. It only
allows a future enrolled host to request an `agent-client` certificate.

Set `OPENBAO_CONFIG_APPLY=false` to inspect the resolved configuration without
writing to OpenBao. Applying configuration requires `OPENBAO_CONFIG_TOKEN`. Initial bootstrap may use
`OPENBAO_ROOT_TOKEN` only with `OPENBAO_CONFIG_BOOTSTRAP=true`.

The SSH mounts only create and configure OpenBao signing CAs. This directory
does not install CA trust in images, change SSH bootstrap, or deploy OpenBao
Agent.
