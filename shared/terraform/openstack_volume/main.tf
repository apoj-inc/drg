resource "openstack_blockstorage_volume_v3" "this" {
  name              = var.name
  size              = var.size
  volume_type       = var.volume_type
  availability_zone = var.availability_zone
  description       = var.description
  metadata          = var.metadata
}

resource "openstack_compute_volume_attach_v2" "this" {
  count = var.instance_id == null ? 0 : 1

  instance_id = var.instance_id
  volume_id   = openstack_blockstorage_volume_v3.this.id
  device      = var.device
}
