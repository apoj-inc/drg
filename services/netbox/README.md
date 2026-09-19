# NetBox service

This directory is a standalone service root. Commands run here affect only NetBox.

## Terraform / OpenTofu

```sh
cd services/netbox/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars
```

CI/CD and local Ansible runs read the same values from environment variables.

For the first bootstrap run, keep `netbox_ipam_enabled=false` so Terraform does
not call the NetBox API before NetBox exists. The first run creates only the
legacy `vmbr1` interface by default. If the
Proxmox SDN bridge already exists and you want to attach the temporary SDN
interface during bootstrap, set `netbox_bootstrap_sdn_enabled=true`.

After NetBox is running, the SDN seed has been applied, and Proxmox SDN has been
reconciled, re-run Terraform with NetBox IPAM enabled:

```powershell
$env:TF_VAR_netbox_ipam_enabled = "true"
$env:TF_VAR_netbox_url = "http://<netbox-api>"
$env:TF_VAR_netbox_token = $env:NETBOX_TOKEN
```

The second run self-registers the NetBox VM, interfaces, and IP addresses in
NetBox. Normal Ansible runs can then use the NetBox dynamic inventory.

## Ansible

```sh
ansible-playbook -i 'netbox,' services/netbox/ansible/netbox.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/netbox.yml \
  -e "ansible_host=192.0.2.21"
```

NetBox is deployed with `netbox-docker` on the A and B contours and listens on
`netbox_http_port`, which is intended to be used by Traefik. Both replicas use
the external Redis service at `redis.example.test:6379`; database `0` is used for
NetBox data and database `1` for the cache.

The cutover removes the local Redis containers. Redis cache contents and queued
RQ jobs are not migrated; perform the switch while background jobs can be
paused or safely retried.

Lifecycle commands and the separate SDN bootstrap playbook are documented in `services/netbox/ansible/README.md`.

## OIDC

Create an OAuth2/OpenID Connect provider in Authentik and set the strict redirect URI to:

```text
<netbox_external_url>/oauth/complete/oidc/
```

Use the provider endpoint as `authentik_issuer_url`, for example:

```text
https://authentik.example.com/application/o/netbox/

The default provider is Authentik. To select Keycloak, set
`netbox.oidc.provider` to `keycloak` and provide
`KEYCLOAK_NETBOX_CLIENT_SECRET`. The Keycloak redirect URI is the same:
`https://netbox.example.test/oauth/complete/oidc/`.
```
