variable "proxmox_api_token" {
  type      = string
  sensitive = true
}

variable "root_password" {
  type      = string
  sensitive = true
}

variable "ssh_public_keys" {
  type      = list(string)
  sensitive = true
}

variable "netbox_token" {
  type      = string
  sensitive = true
}

variable "python_command" {
  type    = string
  default = "python3"
}
