# Docker Hub cache Ansible

Run all commands from the repository root after exporting the required environment variables.

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/dockerhub/ansible/dockerhub.yml --limit dockerhub
```

Client Docker daemon example:

```json
{
  "registry-mirrors": ["https://dockerhub.example.test"]
}
```
