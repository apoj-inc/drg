resource "openstack_networking_network_v2" "this" {
  name           = var.name
  description    = var.description
  tenant_id      = var.project_id
  admin_state_up = var.admin_state_up
  external       = var.external
  shared         = var.shared
  mtu            = var.mtu

  dynamic "segments" {
    for_each = var.network_type == null ? [] : [1]
    content {
      network_type     = var.network_type
      physical_network = var.physical_network
    }
  }
}

resource "openstack_networking_subnet_v2" "this" {
  count = var.subnet == null ? 0 : 1

  name            = var.subnet.name
  tenant_id       = var.project_id
  network_id      = openstack_networking_network_v2.this.id
  cidr            = var.subnet.cidr
  ip_version      = var.subnet.ip_version
  gateway_ip      = try(var.subnet.gateway_ip, null)
  enable_dhcp     = try(var.subnet.enable_dhcp, true)
  dns_nameservers = try(var.subnet.dns_nameservers, [])

  dynamic "allocation_pool" {
    for_each = try(var.subnet.allocation_pool, null) == null ? [] : [var.subnet.allocation_pool]
    content {
      start = allocation_pool.value.start
      end   = allocation_pool.value.end
    }
  }
}

resource "openstack_networking_router_v2" "this" {
  count = var.router == null ? 0 : 1

  name                = var.router.name
  tenant_id           = var.project_id
  admin_state_up      = true
  enable_snat         = try(var.router.enable_snat, true)
  external_network_id = try(var.router.external_network_id, null)
}

resource "openstack_networking_router_interface_v2" "this" {
  count = var.router == null || var.subnet == null ? 0 : 1

  router_id = openstack_networking_router_v2.this[0].id
  subnet_id = openstack_networking_subnet_v2.this[0].id
}

resource "openstack_networking_port_v2" "this" {
  for_each = var.ports

  name                  = each.value.name
  network_id            = openstack_networking_network_v2.this.id
  tenant_id             = var.project_id
  admin_state_up        = true
  security_group_ids    = try(each.value.security_group_ids, [])
  port_security_enabled = try(each.value.port_security_enabled, true)

  dynamic "fixed_ip" {
    for_each = try(each.value.fixed_ip, null) == null || var.subnet == null ? [] : [each.value.fixed_ip]
    content {
      subnet_id  = openstack_networking_subnet_v2.this[0].id
      ip_address = fixed_ip.value
    }
  }
}
