variable "hostname" {
  type = string
}

variable "node_name" {
  type = string
}

variable "vm_id" {
  type = number
}

variable "description" {
  type    = string
  default = null
}

variable "unprivileged" {
  type    = bool
  default = true
}

variable "started" {
  type    = bool
  default = true
}

variable "start_on_boot" {
  type    = bool
  default = true
}

variable "startup_order" {
  description = "Optional Proxmox startup order. Lower values start first."
  type        = number
  default     = null

  validation {
    condition     = var.startup_order == null || var.startup_order >= 0
    error_message = "startup_order must be a non-negative number when configured."
  }
}

variable "feat_nesting" {
  type    = bool
  default = true
}

variable "feat_keyctl" {
  type    = bool
  default = false
}

variable "feat_fuse" {
  type    = bool
  default = false
}

variable "feat_mount" {
  description = "Filesystem types allowed to be mounted inside the LXC container, for example [\"nfs\"]."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for filesystem in var.feat_mount : contains(["cifs", "nfs"], filesystem)])
    error_message = "feat_mount entries must be cifs or nfs."
  }
}

variable "root_password" {
  type      = string
  sensitive = true
}

variable "dns_servers" {
  description = "DNS servers configured in the Proxmox LXC initialization."
  type        = list(string)
  default     = []
}

variable "dns_netbox_hostname" {
  description = "Optional NetBox VM hostname whose primary IPv4 should be used as the DNS server when dns_servers is empty."
  type        = string
  default     = null
}

variable "dns_domain" {
  description = "DNS search domain configured in the Proxmox LXC initialization."
  type        = string
  default     = null
}

variable "ssh_public_keys" {
  type      = list(string)
  sensitive = true
}

variable "cpu_cores" {
  type    = number
  default = 2
}

variable "memory_dedicated" {
  type    = number
  default = 2048
}

variable "memory_swap" {
  type    = number
  default = 512
}

variable "disk_datastore_id" {
  type = string
}

variable "disk_size" {
  type    = number
  default = 10
}

variable "network_interfaces" {
  description = "LXC network interfaces with static or NetBox-managed IP configuration"
  type = list(object({
    name                    = string
    bridge                  = optional(string)
    address                 = optional(string)
    gateway                 = optional(string)
    discover_gateway        = optional(bool, true)
    mtu                     = optional(number)
    mac_address             = optional(string)
    mode                    = optional(string, "static")
    prefix                  = optional(string)
    vrf                     = optional(string)
    vnet                    = optional(string)
    network_name            = optional(string)
    role                    = optional(string)
    dns_name                = optional(string)
    dns_zone                = optional(string)
    dns_ttl                 = optional(number)
    dns_proxy               = optional(bool, false)
    dns_proxy_address       = optional(string)
    dns_proxy_target        = optional(string)
    dns_disable_ptr         = optional(bool)
    disable_ptr             = optional(bool)
    dns_reverse_zone        = optional(string)
    dns_reverse_nameservers = optional(list(string), [])
    dns_reverse_soa_mname   = optional(string)
    dns_reverse_soa_rname   = optional(string)
    ansible_primary         = optional(bool, false)
  }))

  validation {
    condition = alltrue([
      for nic in var.network_interfaces : contains(["static", "netbox"], try(nic.mode, "static"))
    ])
    error_message = "network_interfaces[*].mode must be either static or netbox."
  }

  validation {
    condition = alltrue([
      for nic in var.network_interfaces : try(nic.mode, "static") != "static" || (try(nic.address, null) != null && try(nic.bridge, null) != null)
    ])
    error_message = "Static network interfaces must set both address and bridge."
  }

  validation {
    condition = alltrue([
      for nic in var.network_interfaces : try(nic.mode, "static") != "netbox" || (try(nic.prefix, null) != null && (try(nic.vnet, null) != null || try(nic.network_name, null) != null || try(nic.bridge, null) != null))
    ])
    error_message = "NetBox-managed network interfaces must set prefix and one of vnet, network_name, or bridge."
  }

  validation {
    condition = length([
      for nic in var.network_interfaces : nic.name
      if try(nic.ansible_primary, false)
    ]) <= 1
    error_message = "At most one network interface can set ansible_primary = true."
  }
}

variable "netbox_url" {
  description = "NetBox base URL used for optional IPAM and VM registration"
  type        = string
  default     = null
}

variable "netbox_enabled" {
  description = "Whether to enable NetBox IPAM and VM registration for this container"
  type        = bool
  default     = false
}

variable "netbox_token" {
  description = "NetBox API token used for optional IPAM and VM registration"
  type        = string
  sensitive   = true
  default     = null
}

variable "netbox_validate_certs" {
  description = "Whether to validate NetBox TLS certificates"
  type        = bool
  default     = true
}

variable "netbox_site" {
  description = "NetBox site name for registered virtual machines"
  type        = string
  default     = null
}

variable "netbox_cluster_name" {
  description = "NetBox virtualization cluster name for registered virtual machines"
  type        = string
  default     = null
}

variable "netbox_dns_records_enabled" {
  description = "Whether VM registration should create records in the NetBox DNS plugin"
  type        = bool
  default     = true
}

variable "python_command" {
  description = "Python executable used by Terraform external data sources"
  type        = string
  default     = "python3"
}

variable "ipa_client_enabled" {
  description = "Override platform FreeIPA client enrollment for this guest."
  type        = bool
  default     = null
  nullable    = true
}

variable "lxc_template_path" {
  description = "Existing Proxmox LXC template path, for example local:vztmpl."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[^/:]+:vztmpl$", var.lxc_template_path))
    error_message = "lxc_template_path must be a Proxmox template path such as local:vztmpl."
  }
}

variable "lxc_template_name" {
  description = "Existing LXC template file name in lxc_template_path."
  type        = string
  nullable    = false

  validation {
    condition     = trimspace(var.lxc_template_name) != "" && !strcontains(var.lxc_template_name, "/")
    error_message = "lxc_template_name must be a non-empty file name without a path."
  }
}

variable "os_type" {
  type    = string
  default = "debian"
}

variable "tags" {
  type    = list(string)
  default = []
}
