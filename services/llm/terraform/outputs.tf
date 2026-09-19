output "vm_id" {
  value = module.llm_vm.id
}

output "ansible_primary_ip" {
  value = module.llm_vm.ansible_primary_ip
}

output "gpu_pci_mappings" {
  value = local.service.gpu.pci_mappings
}
