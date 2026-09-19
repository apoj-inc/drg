output "network_id" { value = openstack_networking_network_v2.this.id }
output "network_name" { value = openstack_networking_network_v2.this.name }
output "subnet_id" { value = try(openstack_networking_subnet_v2.this[0].id, null) }
output "subnet_cidr" { value = try(openstack_networking_subnet_v2.this[0].cidr, null) }
output "router_id" { value = try(openstack_networking_router_v2.this[0].id, null) }
output "router_interface_id" { value = try(openstack_networking_router_interface_v2.this[0].id, null) }
output "ports" {
  value = { for name, port in openstack_networking_port_v2.this : name => { id = port.id, fixed_ips = port.all_fixed_ips } }
}
