# Third-party notices

This repository downloads or invokes third-party software at deployment or
build time. It does not relicense those components.

The following components are referenced by the public example release and must
retain their own license and attribution notices when redistributed:

- Ansible and the collections listed in `shared/ansible/collections/requirements.yml`;
- OpenTofu/Terraform and the providers listed in `*/terraform/versions.tf`;
- Dagger and its Python SDK;
- Woodpecker, Forgejo, NetBox, Grafana, Loki, Alloy, Prometheus, Keycloak,
  OpenBao, Traefik, PowerDNS, PostgreSQL, Redis, and other container images
  selected by service configuration;
- Go modules listed in `services/woodpecker/config_extension/go.mod` and
  `go.sum`;
- Python packages used by the validation and Dagger modules.

The exact license inventory must be refreshed when dependency versions change.
