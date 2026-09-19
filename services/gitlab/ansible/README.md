# GitLab Ansible

Run all commands from the repository root after exporting the required environment variables.

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab --tags install
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab --tags configure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab --tags reconfigure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab --tags restart
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab/ansible/gitlab.yml --limit gitlab --tags healthcheck
```
