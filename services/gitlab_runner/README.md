# GitLab service

This directory is a standalone service root. Commands run here affect only GitLab.

## Terraform / OpenTofu

```sh
cd services/gitlab_runner/terraform
tofu init
tofu plan -var-file=proxmox.tfvars
tofu apply -var-file=proxmox.tfvars
```

CI/CD and local Ansible runs read the same values from environment variables.

## Ansible

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab_runner/ansible/gitlab_runner.yml --limit gitlab-runner
```

Lifecycle commands are documented in `services/gitlab_runner/ansible/README.md`.
