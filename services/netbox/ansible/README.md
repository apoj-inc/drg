# NetBox Ansible

Run all commands from the repository root after exporting the required environment variables.

## Application lifecycle

NetBox is the bootstrap exception: before NetBox itself is available, use inline inventory and the legacy bootstrap address.

```sh
ansible-playbook -i 'netbox,' services/netbox/ansible/netbox.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/netbox.yml \
  -e "ansible_host=192.0.2.21"

ansible-playbook -i 'netbox,' services/netbox/ansible/netbox.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/netbox.yml \
  -e "ansible_host=192.0.2.21" \
  --tags install
```

After NetBox is deployed, the SDN bootstrap has seeded the required NetBox
objects, and Proxmox SDN has been reconciled, re-run `services/netbox/terraform`
with `netbox_ipam_enabled=true` and `netbox_token` set. Terraform will
self-register the NetBox VM. After that, normal lifecycle runs can use the
dynamic inventory:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags pull
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags configure
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags deploy
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags bootstrap
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags restart
ansible-playbook -i shared/ansible/inventory/netbox.yml services/netbox/ansible/netbox.yml --limit netbox --tags healthcheck
```

## SDN bootstrap

`config/default_sdn.yml` is a bootstrap seed only. After bootstrap, live NetBox objects are the SDN source of truth.

```sh
ansible-playbook -i 'netbox,' services/netbox/ansible/netbox-sdn-bootstrap.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/netbox.yml \
  -e "ansible_host=192.0.2.21" \
  -e netbox_sdn_bootstrap_confirm=true

ansible-playbook -i 'netbox,' services/netbox/ansible/netbox-sdn-bootstrap.yml \
  -e @shared/ansible/inventory/group_vars/all.yml \
  -e @shared/ansible/inventory/host_vars/netbox.yml \
  -e "ansible_host=192.0.2.21" \
  -e netbox_sdn_bootstrap_confirm=true \
  --tags sdn_bootstrap
```

Without `-e netbox_sdn_bootstrap_confirm=true`, the SDN bootstrap playbook fails before making changes.
