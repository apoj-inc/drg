resource "openstack_networking_port_v2" "this" {
  for_each = var.network_ports

  name               = try(each.value.name, "${var.name}-${each.key}")
  network_id         = each.value.network_id
  security_group_ids = var.security_groups

  dynamic "fixed_ip" {
    for_each = try(each.value.fixed_ip, null) == null ? [] : [each.value.fixed_ip]
    content {
      ip_address = fixed_ip.value
    }
  }
}

resource "openstack_compute_instance_v2" "this" {
  name            = var.name
  image_id        = var.image_id
  flavor_id       = var.flavor_id
  key_pair        = var.key_pair
  user_data       = var.user_data
  metadata        = merge(var.metadata, var.description == null ? {} : { description = var.description })
  config_drive    = var.config_drive
  security_groups = var.security_groups

  dynamic "network" {
    for_each = openstack_networking_port_v2.this
    content {
      port = network.value.id
    }
  }
}

resource "openstack_compute_volume_attach_v2" "this" {
  for_each = var.volumes

  instance_id = openstack_compute_instance_v2.this.id
  volume_id   = each.value.volume_id
  device      = try(each.value.device, null)
}
