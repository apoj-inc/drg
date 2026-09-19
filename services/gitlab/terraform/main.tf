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

module "gitlab_container" {
  for_each = module.config.instances
  source   = "../../../shared/terraform/proxmox_lxc_container"

  startup_order = tonumber(local.service.startup.order)

  hostname              = each.key
  node_name             = each.value.node
  vm_id                 = each.value.vm_id
  description           = local.service.service.description
  feat_nesting          = local.service.container.features.nesting
  feat_keyctl           = local.service.container.features.keyctl
  feat_fuse             = local.service.container.features.fuse
  network_interfaces    = module.config.instance_network_interfaces[each.key]
  root_password         = var.root_password
  dns_servers           = module.config.dns_servers
  dns_domain            = try(local.platform.dns.domain, null)
  ssh_public_keys       = var.ssh_public_keys
  cpu_cores             = local.service.compute.cpu_cores
  memory_dedicated      = local.service.compute.memory_mb
  memory_swap           = local.service.compute.swap_mb
  disk_datastore_id     = local.cluster.defaults.disk_datastore
  disk_size             = local.service.compute.disk_gb
  lxc_template_path     = local.service.container.template.path
  lxc_template_name     = local.service.container.template.name
  os_type               = "debian"
  tags                  = concat(local.service.service.tags, ["contour-${lower(each.value.contour)}"])
  netbox_enabled        = try(local.platform.netbox.enabled, false)
  netbox_url            = local.platform.netbox.url
  netbox_token          = var.netbox_token
  netbox_validate_certs = local.platform.netbox.validate_certs
  netbox_site           = local.platform.netbox.site
  netbox_cluster_name   = local.platform.netbox.cluster_by_proxmox_cluster[var.deployment_cluster]
  python_command        = var.python_command
}
