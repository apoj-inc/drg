variable "project_id" {
  description = "Project that owns the security groups."
  type        = string
  default     = null
}

variable "groups" {
  type = map(object({
    description = string
    rules = optional(map(object({
      direction         = optional(string, "ingress")
      ethertype         = optional(string, "IPv4")
      protocol          = optional(string)
      port_range_min    = optional(number)
      port_range_max    = optional(number)
      remote_ip_prefix  = optional(string)
      remote_group_id   = optional(string)
      remote_group_name = optional(string)
      description       = optional(string)
    })), {})
  }))
}
