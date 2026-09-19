# Grafana Ansible

Run all commands from the repository root after exporting the required environment variables.

The service defaults to running Grafana as a Docker Compose application.
Set `grafana.install_method: package` or `grafana.install_method: source` only for fallback installs.

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/grafana/ansible/grafana.yml --limit grafana
ansible-playbook -i shared/ansible/inventory/netbox.yml services/grafana/ansible/grafana.yml --limit grafana --tags install
ansible-playbook -i shared/ansible/inventory/netbox.yml services/grafana/ansible/grafana.yml --limit grafana --tags configure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/grafana/ansible/grafana.yml --limit grafana --tags restart
```
