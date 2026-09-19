output "vm_id" {
  value = proxmox_virtual_environment_vm.this.vm_id
}

output "id" {
  value = proxmox_virtual_environment_vm.this.id
}

output "image_url" {
  value = var.image_url
}
