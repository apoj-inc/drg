# Traefik service

This directory is a standalone service root. Commands run here affect only Traefik.

## Terraform / OpenTofu

```sh
cd services/traefik/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars
```

CI/CD and local Ansible runs read the same values from environment variables.

## Ansible

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik
```

Traefik is deployed as one LXC per rollout contour with Docker Compose in host
network mode. The contour instances share the configured ingress VIP through
the systemd `keepalived` service; only the healthy VRRP master owns the VIP.
Traefik listens on the HTTP/HTTPS ports plus TCP/UDP entrypoints generated from
service route definitions.

The generated configuration follows the existing Traefik LXC on `192.0.2.2`: DNS challenge with Selectel, wildcard TLS for `traefik_domain`, shared file-provider middlewares, and dynamic route files. The VIP and contour placement are declared in `services/traefik/config.yaml`; real instance addresses are allocated through NetBox.

The canonical `traefik.example.test` A record is reconciled by the shared service
DNS pipeline to the configured VIP, just like the canonical records for GitLab
and NetBox.

Reserve `198.51.100.3` in the NetBox management prefix before applying either
contour. The Ansible play rejects a dynamically allocated instance address that
matches the VIP.

Traefik middleware definitions are kept in `services/traefik/middlewares.yml`
and are rendered to
`/opt/traefik/config/middlewares.yml` by the Ansible role. Middleware values may
use Ansible variables, such as `authentik_url`.

Service routes are defined in `services/<service>/traefik.yml`; Jinja-enabled routes use `traefik.yml.j2`. The centralized `traefik-config.yml` playbook renders them to the Traefik hosts. HTTP routes define `host` and `backend_port`; TCP/UDP routes define a fixed `listen_port` and `backend_port`. The backend IP is taken from the service VM primary IPv4 address returned by the NetBox inventory.

Routes for services without a NetBox record are defined in
`services/traefik/external-services.yml`. This file is processed directly on
the Traefik host, so it does not require an inventory host or a NetBox tag.
HTTP routes must provide `backend_url`; TCP/UDP routes must provide
`backend_address`:

```yaml
http:
  - name: external-app
    host: app.example.test
    backend_url: http://192.0.2.50:8080
tcp:
  - name: external-ssh
    listen_port: 2224
    backend_address: 192.0.2.50:22
```

The file is rendered to `/opt/traefik/config/external-services.yml`.

The generated Traefik pipeline runs `traefik-config.yml` against the NetBox inventory. The playbook processes only VMs whose NetBox data contains the `traefik-managed-route` tag; their route files are delegated to the Traefik host.

Example:

```yaml
http:
  - name: example
    host: example.example.test
    backend_port: 8080
tcp:
  - name: example-ssh
    listen_port: 2223
    backend_port: 22
udp:
  - name: example-dns
    listen_port: 5353
    backend_port: 53
```
The role writes one dynamic configuration file per service under:

```text
/opt/traefik/config
```

Set `traefik_dashboard_host` to expose the Traefik dashboard through HTTPS; its route is defined in `services/traefik/traefik.yml`.
Lifecycle commands are documented in `services/traefik/ansible/README.md`.

Each service may define `services/<service>/traefik.yml`, or `traefik.yml.j2` when inventory-dependent Jinja is required. HTTP routes use `backend_port`; TCP and UDP routes use a fixed `listen_port` and `backend_port`. The backend IP is taken from the service VM primary IPv4 address returned by the NetBox inventory. TCP/UDP listen ports are aggregated into Traefik static entrypoints and checked for duplicate or occupied ports before deployment.
