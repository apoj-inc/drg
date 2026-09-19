output "ids" {
  value = { for name, group in openstack_networking_secgroup_v2.this : name => group.id }
}
