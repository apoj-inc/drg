output "id" {
  value = proxmox_virtual_environment_container.this.id
}

output "ipv4" {
  value = proxmox_virtual_environment_container.this.ipv4
}

output "network_interfaces" {
  value = local.network_interfaces
}

output "ansible_primary_ip" {
  value = local.ansible_primary_ip
}

output "netbox_vm_id" {
  value = null
}

output "template_filename" {
  value = var.lxc_template_name
}
