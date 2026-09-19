resource "openstack_images_image_v2" "this" {
  name             = var.name
  image_source_url = var.image_source_url
  verify_checksum  = true
  disk_format      = var.disk_format
  container_format = var.container_format
  visibility       = var.visibility
  min_disk_gb      = var.min_disk_gb
  min_ram_mb       = var.min_ram_mb
  properties = merge(var.properties, {
    source_checksum      = var.image_source_checksum
    source_checksum_algo = var.image_source_checksum_algo
  })
}
