variable "node_name" {
  type = string
}

variable "vm_id" {
  type = number
}

variable "name" {
  type = string
}

variable "image_url" {
  description = "Explicit URL of the GenericCloud QCOW2 image used to build this template."
  type        = string
}

variable "image_filename" {
  description = "Explicit filename used for the downloaded cloud image in Proxmox storage."
  type        = string
}

variable "image_datastore_id" {
  description = "Proxmox datastore that supports the import content type."
  type        = string
}

variable "disk_datastore_id" {
  description = "Datastore for the template boot disk. Linked clones must use storage compatible with this datastore."
  type        = string
}

variable "cloud_init_datastore_id" {
  description = "Datastore for the template cloud-init disk."
  type        = string
}

variable "disk_size" {
  type    = number
  default = 8
}

variable "disk_read_limit_mbps" {
  description = "Maximum read throughput for the template boot disk in MB/s."
  type        = number
  default     = 200

  validation {
    condition     = var.disk_read_limit_mbps >= 0
    error_message = "disk_read_limit_mbps must be zero or greater."
  }
}

variable "disk_write_limit_mbps" {
  description = "Maximum write throughput for the template boot disk in MB/s."
  type        = number
  default     = 100

  validation {
    condition     = var.disk_write_limit_mbps >= 0
    error_message = "disk_write_limit_mbps must be zero or greater."
  }
}

variable "tags" {
  type    = list(string)
  default = ["opentofu", "cloud-init-template"]
}
