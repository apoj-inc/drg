data "external" "netbox_sdn" {
  program = [var.python_command, "${path.module}/scripts/netbox_sdn.py"]

  query = {
    netbox_url                        = var.netbox_url
    netbox_token                      = var.netbox_token
    netbox_validate_certs             = tostring(var.netbox_validate_certs)
    netbox_config_context_name        = var.netbox_config_context_name
    platform_nodes                    = jsonencode(yamldecode(file(var.platform_config)).clusters[var.deployment_cluster].nodes)
    platform_fabric_prefix            = yamldecode(file(var.platform_config)).clusters[var.deployment_cluster].defaults.openfabric_fabric_prefix
    platform_exit_nodes               = jsonencode(yamldecode(file(var.platform_config)).clusters[var.deployment_cluster].defaults.openfabric_exit_nodes)
    platform_primary_exit_node        = yamldecode(file(var.platform_config)).clusters[var.deployment_cluster].defaults.openfabric_primary_exit_node
    platform_exit_nodes_local_routing = tostring(yamldecode(file(var.platform_config)).clusters[var.deployment_cluster].defaults.openfabric_exit_nodes_local_routing)
  }
}

resource "proxmox_sdn_applier" "finalizer" {
}

resource "proxmox_sdn_fabric_openfabric" "this" {
  for_each = local.fabric.enabled ? { (local.fabric.id) = local.fabric } : {}

  id        = each.value.id
  ip_prefix = each.value.ip_prefix

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_fabric_node_openfabric" "this" {
  for_each = local.nodes

  fabric_id       = proxmox_sdn_fabric_openfabric.this[each.value.fabric_id].id
  node_id         = each.value.id
  ip              = each.value.ip
  interface_names = each.value.interface_names

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_controller_evpn" "this" {
  for_each = local.controllers

  id     = each.value.id
  asn    = each.value.asn
  fabric = proxmox_sdn_fabric_openfabric.this[each.value.fabric_id].id

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_zone_evpn" "this" {
  for_each = local.zones

  id         = each.value.id
  controller = proxmox_sdn_controller_evpn.this[each.value.controller_id].id
  vrf_vxlan  = each.value.vrf_vxlan
  nodes      = each.value.nodes
  mtu        = each.value.mtu
  ipam       = each.value.ipam
  rt_import  = each.value.rt_import

  exit_nodes               = each.value.exit_nodes
  exit_nodes_local_routing = each.value.exit_nodes_local_routing
  primary_exit_node        = each.value.primary_exit_node

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_vnet" "this" {
  for_each = local.vnets

  id    = each.value.id
  zone  = proxmox_sdn_zone_evpn.this[each.value.zone_id].id
  alias = each.value.alias
  tag   = each.value.vni

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_subnet" "this" {
  for_each = local.subnets

  cidr    = each.value.cidr
  vnet    = proxmox_sdn_vnet.this[each.value.vnet_id].id
  gateway = each.value.gateway
  snat    = each.value.snat

  depends_on = [
    proxmox_sdn_applier.finalizer,
  ]
}

resource "proxmox_sdn_applier" "this" {
  lifecycle {
    replace_triggered_by = [
      terraform_data.sdn_model_version,
    ]
  }

  depends_on = [
    proxmox_sdn_fabric_openfabric.this,
    proxmox_sdn_fabric_node_openfabric.this,
    proxmox_sdn_controller_evpn.this,
    proxmox_sdn_zone_evpn.this,
    proxmox_sdn_vnet.this,
    proxmox_sdn_subnet.this,
  ]
}

resource "terraform_data" "sdn_model_version" {
  input = sha256(jsonencode(local.netbox_sdn_model))
}
