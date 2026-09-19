locals {
  platform = yamldecode(file(var.platform_config))
  cluster  = local.platform.clusters[var.deployment_cluster]

  # The image download and the template VM use the same explicitly selected
  # node. The platform configuration is the single source of truth.
  image_node = local.cluster.defaults.cloudinit_template_node
  images     = local.cluster.defaults.cloudinit_images
}

module "debian_13_cloudinit_template" {
  source = "../../../shared/terraform/proxmox_vm_cloudinit_template"

  node_name               = local.image_node
  vm_id                   = var.debian_cloud_template_vm_id
  name                    = "debian-13-cloudinit-template"
  image_url               = local.images.debian_13.url
  image_filename          = local.images.debian_13.filename
  image_datastore_id      = var.image_datastore_id
  disk_datastore_id       = var.disk_datastore_id
  cloud_init_datastore_id = var.cloud_init_datastore_id
  tags                    = ["opentofu", "debian-13", "cloud-init-template"]
}

module "fedora_44_cloudinit_template" {
  source = "../../../shared/terraform/proxmox_vm_cloudinit_template"

  node_name               = local.image_node
  vm_id                   = var.fedora_cloud_template_vm_id
  name                    = "fedora-44-cloudinit-template"
  image_url               = local.images.fedora_44.url
  image_filename          = local.images.fedora_44.filename
  image_datastore_id      = var.image_datastore_id
  disk_datastore_id       = var.disk_datastore_id
  cloud_init_datastore_id = var.cloud_init_datastore_id
  tags                    = ["opentofu", "fedora-44", "cloud-init-template"]
}

output "template_vm_id" {
  value = module.debian_13_cloudinit_template.vm_id
}

output "fedora_template_vm_id" {
  value = module.fedora_44_cloudinit_template.vm_id
}
