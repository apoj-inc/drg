variable "name" {
  description = "OpenStack project name."
  type        = string
}

variable "description" {
  description = "OpenStack project description."
  type        = string
  default     = null
}

variable "domain_id" {
  description = "Keystone domain ID for the project."
  type        = string
  default     = null
}

variable "enabled" {
  description = "Whether the project is enabled."
  type        = bool
  default     = true
}

variable "application_credentials" {
  description = "Application credentials to create, keyed by credential name."
  type = map(object({
    description  = optional(string)
    roles        = optional(set(string), [])
    unrestricted = optional(bool, false)
  }))
  default = {}
}

variable "compute_quota" {
  description = "Optional Nova quota values. Omitted values keep provider defaults."
  type = object({
    cores                = optional(number)
    instances            = optional(number)
    ram                  = optional(number)
    key_pairs            = optional(number)
    server_groups        = optional(number)
    server_group_members = optional(number)
  })
  default = null
}

variable "network_quota" {
  description = "Optional Neutron quota values."
  type = object({
    network             = optional(number)
    subnet              = optional(number)
    port                = optional(number)
    router              = optional(number)
    floatingip          = optional(number)
    security_group      = optional(number)
    security_group_rule = optional(number)
  })
  default = null
}

variable "volume_quota" {
  description = "Optional Cinder quota values."
  type = object({
    volumes          = optional(number)
    snapshots        = optional(number)
    gigabytes        = optional(number)
    backups          = optional(number)
    backup_gigabytes = optional(number)
  })
  default = null
}
