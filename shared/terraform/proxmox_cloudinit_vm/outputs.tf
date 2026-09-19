output "id" { value = proxmox_virtual_environment_vm.this.id }
output "ipv4" { value = proxmox_virtual_environment_vm.this.ipv4_addresses }
output "network_interfaces" { value = local.network_interfaces }
output "ansible_primary_ip" { value = local.ansible_primary_ip }
output "netbox_vm_id" { value = null }
output "template_vm_id" { value = var.template_vm_id }
