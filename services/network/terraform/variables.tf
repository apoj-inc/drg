variable "proxmox_api_token" {
  description = "Proxmox API token"
  type        = string
  sensitive   = true
}

variable "proxmox_insecure" {
  description = "Whether to skip TLS verification for Proxmox"
  type        = bool
  default     = true
}

variable "platform_config" {
  description = "Platform YAML file containing the authoritative Proxmox node names."
  type        = string
  default     = "../../../environments/example/platform.yaml"
}

variable "deployment_cluster" {
  description = "Platform cluster whose nodes are managed by this SDN configuration."
  type        = string
  default     = "example-cluster"
}

variable "netbox_url" {
  description = "NetBox base URL"
  type        = string
}

variable "netbox_token" {
  description = "NetBox API token"
  type        = string
  sensitive   = true
}

variable "netbox_validate_certs" {
  description = "Whether to validate NetBox TLS certificates"
  type        = bool
  default     = true
}

variable "netbox_config_context_name" {
  description = "NetBox config context containing Terraform SDN selectors"
  type        = string
  default     = "proxmox-sdn-terraform-selectors"
}

variable "python_command" {
  description = "Python executable used by Terraform external data source"
  type        = string
  default     = "python3"
}
