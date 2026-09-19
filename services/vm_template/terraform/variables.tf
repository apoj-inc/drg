variable "proxmox_api_token" {
  type      = string
  sensitive = true
}

variable "proxmox_insecure" {
  type    = bool
  default = false
}

variable "platform_config" {
  description = "Platform YAML file used to discover the cloud-init template node."
  type        = string
  default     = "../../../environments/example/platform.yaml"
}

variable "deployment_cluster" {
  description = "Platform cluster whose configured template node hosts the cloud images."
  type        = string
  default     = "example-cluster"
}

variable "debian_cloud_template_vm_id" {
  description = "Dedicated Proxmox VM ID of the shared Debian cloud-init template."
  type        = number
  default     = 9000
}

variable "fedora_cloud_template_vm_id" {
  description = "Dedicated Proxmox VM ID of the Fedora 44 cloud-init template."
  type        = number
  default     = 9001
}

variable "image_datastore_id" {
  description = "Datastore supporting Proxmox import content."
  type        = string
  default     = "local"
}

variable "disk_datastore_id" {
  description = "Datastore that stores the template disk and supports linked clones."
  type        = string
  default     = "local-lvm"
}

variable "cloud_init_datastore_id" {
  description = "Datastore for the cloud-init disk."
  type        = string
  default     = "local-lvm"
}
