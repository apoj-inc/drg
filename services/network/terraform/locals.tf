locals {
  platform = yamldecode(file(var.platform_config))
  cluster  = local.platform.clusters[var.deployment_cluster]

  netbox_sdn_model = jsondecode(data.external.netbox_sdn.result.model)

  fabric      = local.netbox_sdn_model.fabric
  controllers = local.netbox_sdn_model.controllers
  nodes       = local.netbox_sdn_model.nodes
  zones       = local.netbox_sdn_model.zones
  vnets       = local.netbox_sdn_model.vnets
  subnets     = local.netbox_sdn_model.subnets
}
