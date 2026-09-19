output "id" { value = openstack_compute_instance_v2.this.id }
output "name" { value = openstack_compute_instance_v2.this.name }
output "ports" {
  value = { for name, port in openstack_networking_port_v2.this : name => { id = port.id, fixed_ips = port.all_fixed_ips } }
}
