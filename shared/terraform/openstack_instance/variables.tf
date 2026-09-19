variable "name" { type = string }
variable "description" {
  type    = string
  default = null
}
variable "image_id" { type = string }
variable "flavor_id" { type = string }
variable "key_pair" {
  type    = string
  default = null
}
variable "user_data" {
  type    = string
  default = null
}
variable "metadata" {
  type    = map(string)
  default = {}
}
variable "security_groups" {
  type    = set(string)
  default = []
}
variable "config_drive" {
  type    = bool
  default = true
}
variable "network_ports" {
  type = map(object({
    network_id = string
    fixed_ip   = optional(string)
    name       = optional(string)
  }))
  default = {}
}
variable "volumes" {
  type = map(object({
    volume_id = string
    device    = optional(string)
  }))
  default = {}
}
