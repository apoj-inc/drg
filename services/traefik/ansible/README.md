# Traefik Ansible

The play targets the `traefik` inventory group, which contains both contour
instances. Keepalived discovers peer ingress addresses from gathered network
facts and configures the VIP from `services/traefik/config.yaml`.

Run all commands from the repository root after exporting the required environment variables.

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags install
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags pull
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags configure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags deploy
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags restart
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags reload
ansible-playbook -i shared/ansible/inventory/netbox.yml services/traefik/ansible/traefik.yml --limit tags_traefik --tags healthcheck
```
