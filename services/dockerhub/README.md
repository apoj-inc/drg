# Docker Hub cache service

This directory is a standalone service root for a Docker Hub pull-through cache backed by the official Docker Registry image.

## Terraform / OpenTofu

DockerHub uses NetBox IPAM for its primary SDN interface and registers all VM addresses in NetBox. Export the NetBox token before running Terraform:

```powershell
$env:TF_VAR_netbox_token = $env:NETBOX_TOKEN
```

```sh
cd services/dockerhub/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars
```

CI/CD and local Ansible runs read the same values from environment variables.

## Ansible

```sh
ansible-inventory -i shared/ansible/inventory/netbox.yml --graph
ansible-playbook -i shared/ansible/inventory/netbox.yml services/dockerhub/ansible/dockerhub.yml --limit dockerhub
```

The registry listens on `dockerhub_http_port`, intended for Traefik or another reverse proxy. Set `dockerhub_proxy_username` and `dockerhub_proxy_password` only if the cache should authenticate to Docker Hub for upstream pulls.
