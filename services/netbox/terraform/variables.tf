variable "proxmox_api_token" {
  description = "Proxmox API key"
  type        = string
  sensitive   = true
}

variable "ssh_public_keys" {
  type      = list(string)
  sensitive = true
}

variable "root_password" {
  type      = string
  sensitive = true
}

variable "netbox_token" {
  description = "NetBox API token used for Terraform IPAM and VM registration"
  type        = string
  sensitive   = true
  default     = null
}

variable "python_command" {
  description = "Python executable used by Terraform external data sources"
  type        = string
  default     = "python3"
}

variable "deployment_cluster" {
  type    = string
  default = "example-cluster"
}

variable "deployment_contour" {
  type    = string
  default = "A"
}
