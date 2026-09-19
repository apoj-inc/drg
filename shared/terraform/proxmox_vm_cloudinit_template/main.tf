resource "proxmox_virtual_environment_download_file" "cloud_image" {
  content_type = "import"
  datastore_id = var.image_datastore_id
  node_name    = var.node_name

  url       = var.image_url
  file_name = var.image_filename

  overwrite_unmanaged = false
}

resource "proxmox_virtual_environment_vm" "this" {
  name        = var.name
  description = "Cloud-init template managed by OpenTofu. Source image: ${var.image_url}"
  node_name   = var.node_name
  vm_id       = var.vm_id

  template = true
  started  = false

  agent {
    enabled = true
  }

  cpu {
    cores = 1
    type  = "host"
  }

  memory {
    dedicated = 512
    floating  = 512
  }

  scsi_hardware = "virtio-scsi-single"

  disk {
    datastore_id = var.disk_datastore_id
    import_from  = proxmox_virtual_environment_download_file.cloud_image.id
    interface    = "scsi0"
    size         = var.disk_size
    iothread     = true
    discard      = "on"

    speed {
      read  = var.disk_read_limit_mbps
      write = var.disk_write_limit_mbps
    }
  }

  initialization {
    datastore_id = var.cloud_init_datastore_id

    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }
  }

  network_device {
    model = "virtio"
  }

  tags = var.tags
}
