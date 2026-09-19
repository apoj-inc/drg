module "config" {
  source = "../../../shared/terraform/service_config"

  platform_config_file = abspath("${path.module}/../../../environments/example/platform.yaml")
  service_config_file  = abspath("${path.module}/../config.yaml")
  cluster              = var.deployment_cluster
  contour              = var.deployment_contour
}

locals {
  platform = yamldecode(file("${path.module}/../../../environments/example/platform.yaml"))
  service  = yamldecode(file("${path.module}/../config.yaml"))
  cluster  = local.platform.clusters[var.deployment_cluster]
}

module "freeipa_instance" {
  for_each = module.config.instances

  source = "../../../shared/terraform/proxmox_cloudinit_vm"

  startup_order = tonumber(local.service.startup.order)

  hostname                   = each.key
  node_name                  = each.value.node
  vm_id                      = each.value.vm_id
  template_vm_id             = try(local.service.vm_template.cloudinit_template_vm_id, local.cluster.defaults.cloudinit_template_vm_id)
  description                = local.service.service.description
  network_interfaces         = module.config.instance_network_interfaces[each.key]
  root_password              = var.root_password
  dns_servers                = module.config.dns_servers
  dns_domain                 = try(local.platform.dns.domain, null)
  ssh_public_keys            = var.ssh_public_keys
  cpu_cores                  = local.service.compute.cpu_cores
  memory_dedicated           = local.service.compute.memory_mb
  disk_datastore_id          = local.cluster.defaults.disk_datastore
  cloud_init_datastore_id    = local.cluster.defaults.cloudinit_datastore
  disk_size                  = local.service.compute.disk_gb
  tags                       = concat(local.service.service.tags, ["contour-${lower(each.value.contour)}"])
  netbox_enabled             = try(local.platform.netbox.enabled, false)
  netbox_url                 = local.platform.netbox.url
  netbox_token               = var.netbox_token
  netbox_validate_certs      = local.platform.netbox.validate_certs
  netbox_dns_records_enabled = true
  netbox_site                = local.platform.netbox.site
  netbox_cluster_name        = local.platform.netbox.cluster_by_proxmox_cluster[var.deployment_cluster]
  python_command             = var.python_command
  ipa_client_enabled         = false
}
