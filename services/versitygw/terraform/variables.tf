variable "proxmox_api_token" {
  description = "Proxmox API token supplied at runtime from OpenBao."
  type        = string
  sensitive   = true
}

variable "ssh_public_keys" {
  description = "SSH public keys supplied at runtime from OpenBao."
  type        = list(string)
  sensitive   = true
}

variable "root_password" {
  description = "VM root password supplied at runtime from OpenBao."
  type        = string
  sensitive   = true
}

variable "netbox_token" {
  description = "NetBox API token supplied at runtime from OpenBao."
  type        = string
  sensitive   = true
}

variable "deployment_cluster" {
  description = "Platform cluster for this Terraform state."
  type        = string
  default     = "example-cluster"
}

variable "deployment_contour" {
  description = "Platform rollout contour for this Terraform state."
  type        = string
  default     = "A"
}

variable "python_command" {
  description = "Python executable used by Terraform external data sources."
  type        = string
  default     = "python3"
}
