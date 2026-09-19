# GitLab Ansible

Run all commands from the repository root after exporting the required environment variables.

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab_runner/ansible/gitlab_runner.yml --limit gitlab-runner
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab_runner/ansible/gitlab_runner.yml --limit gitlab-runner --tags install
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab_runner/ansible/gitlab_runner.yml --limit gitlab-runner --tags configure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/gitlab_runner/ansible/gitlab_runner.yml --limit gitlab-runner --tags restart
```
