variable "name" { type = string }
variable "description" {
  type    = string
  default = null
}
variable "project_id" {
  type    = string
  default = null
}
variable "external" {
  type    = bool
  default = false
}
variable "shared" {
  type    = bool
  default = false
}
variable "admin_state_up" {
  type    = bool
  default = true
}
variable "mtu" {
  type    = number
  default = null
}
variable "network_type" {
  type    = string
  default = null
}
variable "physical_network" {
  type    = string
  default = null
}

variable "subnet" {
  type = object({
    name            = string
    cidr            = string
    ip_version      = optional(number, 4)
    gateway_ip      = optional(string)
    enable_dhcp     = optional(bool, true)
    dns_nameservers = optional(list(string), [])
    allocation_pool = optional(object({
      start = string
      end   = string
    }))
  })
  default = null
}

variable "router" {
  type = object({
    name                = string
    external_network_id = optional(string)
    enable_snat         = optional(bool, true)
  })
  default = null
}

variable "ports" {
  type = map(object({
    name                  = string
    fixed_ip              = optional(string)
    security_group_ids    = optional(set(string), [])
    port_security_enabled = optional(bool, true)
  }))
  default = {}
}
