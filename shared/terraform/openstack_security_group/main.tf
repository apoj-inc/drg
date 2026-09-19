resource "openstack_networking_secgroup_v2" "this" {
  for_each    = var.groups
  name        = each.key
  description = each.value.description
  tenant_id   = var.project_id
}

locals {
  rules = merge([
    for group_name, group in var.groups : {
      for rule_name, rule in try(group.rules, {}) : "${group_name}.${rule_name}" => merge(rule, {
        security_group_id = openstack_networking_secgroup_v2.this[group_name].id
        remote_group_id = try(
          openstack_networking_secgroup_v2.this[rule.remote_group_name].id,
          try(rule.remote_group_id, null),
        )
      })
    }
  ]...)
}

resource "openstack_networking_secgroup_rule_v2" "this" {
  for_each = local.rules

  security_group_id = each.value.security_group_id
  direction         = try(each.value.direction, "ingress")
  ethertype         = try(each.value.ethertype, "IPv4")
  protocol          = try(each.value.protocol, null)
  port_range_min    = try(each.value.port_range_min, null)
  port_range_max    = try(each.value.port_range_max, null)
  remote_ip_prefix  = try(each.value.remote_ip_prefix, null)
  remote_group_id   = try(each.value.remote_group_id, null)
  description       = try(each.value.description, null)
}
