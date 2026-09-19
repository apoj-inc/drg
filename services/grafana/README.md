# Grafana service

This directory is a standalone service root. Commands run here affect only Grafana.

## Terraform / OpenTofu

```sh
cd services/grafana/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars
```

CI/CD and local Ansible runs read the same values from environment variables.

## Ansible

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/grafana/ansible/grafana.yml --limit grafana
```

Lifecycle commands are documented in `services/grafana/ansible/README.md`.
