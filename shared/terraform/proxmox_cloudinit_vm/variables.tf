variable "hostname" { type = string }
variable "node_name" { type = string }
variable "vm_id" { type = number }
variable "template_vm_id" { type = number }
variable "description" {
  type    = string
  default = null
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
variable "root_password" {
  type      = string
  sensitive = true
}
variable "ssh_public_keys" {
  type      = list(string)
  sensitive = true
}
variable "dns_servers" {
  type    = list(string)
  default = []
}
variable "dns_netbox_hostname" {
  type    = string
  default = null
}
variable "dns_domain" {
  type    = string
  default = null
}
variable "cpu_cores" {
  type    = number
  default = 2
}

variable "cpu_numa" {
  description = "Enable NUMA awareness for the guest CPU."
  type        = bool
  default     = false
}

variable "machine" {
  description = "QEMU machine type. PCIe passthrough requires q35."
  type        = string
  default     = "pc"

  validation {
    condition     = contains(["pc", "q35"], var.machine)
    error_message = "machine must be pc or q35."
  }
}

variable "bios" {
  description = "Guest firmware implementation."
  type        = string
  default     = "seabios"

  validation {
    condition     = contains(["seabios", "ovmf"], var.bios)
    error_message = "bios must be seabios or ovmf."
  }
}

variable "efi_datastore_id" {
  description = "Datastore for the OVMF EFI variables disk."
  type        = string
  default     = null
  nullable    = true
}

variable "vga_type" {
  description = "Virtual display retained independently of passed-through GPUs."
  type        = string
  default     = "std"

  validation {
    condition     = contains(["std", "virtio", "serial0", "none"], var.vga_type)
    error_message = "vga_type must be std, virtio, serial0, or none."
  }
}

variable "pci_mappings" {
  description = "Ordered Proxmox cluster PCI mapping names attached as hostpci0..hostpci15. Raw PCI addresses are intentionally unsupported for API-token authentication."
  type        = list(string)
  default     = []

  validation {
    condition = (
      length(var.pci_mappings) <= 16 &&
      length(distinct(var.pci_mappings)) == length(var.pci_mappings) &&
      alltrue([for mapping in var.pci_mappings : can(regex("^[a-z][a-z0-9_-]*$", mapping))])
    )
    error_message = "pci_mappings must contain at most 16 unique lower-case Proxmox mapping names."
  }
}

variable "numa_nodes" {
  description = "Optional explicit guest-to-host NUMA placement. Leave empty until host topology has been audited."
  type = list(object({
    device    = string
    cpus      = string
    memory    = number
    hostnodes = optional(string)
    policy    = optional(string, "preferred")
  }))
  default = []

  validation {
    condition = (
      length(var.numa_nodes) <= 8 &&
      length(distinct([for node in var.numa_nodes : node.device])) == length(var.numa_nodes) &&
      alltrue([for node in var.numa_nodes : can(regex("^numa[0-7]$", node.device))]) &&
      alltrue([for node in var.numa_nodes : contains(["preferred", "bind", "interleave"], node.policy)])
    )
    error_message = "numa_nodes must use unique-compatible numa0..numa7 devices and a supported policy."
  }
}
variable "memory_dedicated" {
  type    = number
  default = 2048
}

variable "memory_min_mb" {
  description = "Minimum VM memory in MiB for ballooning. Defaults to dedicated memory."
  type        = number
  default     = null
  nullable    = true

  validation {
    condition     = var.memory_min_mb == null || (var.memory_min_mb == 0 || (var.memory_min_mb >= 16 && floor(var.memory_min_mb) == var.memory_min_mb))
    error_message = "memory_min_mb must be null, 0 to disable ballooning, or a whole number of at least 16 MiB."
  }
}
variable "disk_datastore_id" { type = string }
variable "cloud_init_datastore_id" { type = string }
variable "disk_size" {
  description = "Boot disk size in GiB."
  type        = number
  default     = 10
}

variable "data_disk_datastore_id" {
  description = "Optional datastore for the separate data disk. Defaults to the boot disk datastore."
  type        = string
  default     = null
  nullable    = true
}

variable "data_disk_size" {
  description = "Optional separate data disk size in GiB. When null, no data disk is attached."
  type        = number
  default     = null
  nullable    = true

  validation {
    condition     = var.data_disk_size == null || (var.data_disk_size > 0 && floor(var.data_disk_size) == var.data_disk_size)
    error_message = "data_disk_size must be null or a positive whole number of GiB."
  }
}

variable "disk_read_limit_mbps" {
  description = "Maximum read throughput for the VM boot disk in MB/s."
  type        = number
  default     = 200

  validation {
    condition     = var.disk_read_limit_mbps >= 0
    error_message = "disk_read_limit_mbps must be zero or greater."
  }
}

variable "disk_write_limit_mbps" {
  description = "Maximum write throughput for the VM boot disk in MB/s."
  type        = number
  default     = 100

  validation {
    condition     = var.disk_write_limit_mbps >= 0
    error_message = "disk_write_limit_mbps must be zero or greater."
  }
}

variable "tags" {
  type    = list(string)
  default = []
}
variable "netbox_url" {
  type    = string
  default = null
}
variable "netbox_enabled" {
  type    = bool
  default = false
}
variable "netbox_token" {
  type      = string
  sensitive = true
  default   = null
}
variable "netbox_validate_certs" {
  type    = bool
  default = true
}
variable "netbox_site" {
  type    = string
  default = null
}
variable "netbox_cluster_name" {
  type    = string
  default = null
}
variable "netbox_dns_records_enabled" {
  type    = bool
  default = true
}
variable "python_command" {
  type    = string
  default = "python3"
}

variable "ipa_client_enabled" {
  description = "Override platform FreeIPA client enrollment for this guest."
  type        = bool
  default     = null
  nullable    = true
}

variable "network_interfaces" {
  description = "VM network interfaces with static or NetBox-managed cloud-init configuration."
  type = list(object({
    name                    = string
    bridge                  = optional(string)
    address                 = optional(string)
    gateway                 = optional(string)
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
    condition     = alltrue([for nic in var.network_interfaces : contains(["static", "netbox"], try(nic.mode, "static"))])
    error_message = "network_interfaces[*].mode must be either static or netbox."
  }

  validation {
    condition     = alltrue([for nic in var.network_interfaces : try(nic.mode, "static") != "static" || (try(nic.address, null) != null && try(nic.bridge, null) != null)])
    error_message = "Static network interfaces must set both address and bridge."
  }

  validation {
    condition     = alltrue([for nic in var.network_interfaces : try(nic.mode, "static") != "netbox" || (try(nic.prefix, null) != null && (try(nic.vnet, null) != null || try(nic.network_name, null) != null || try(nic.bridge, null) != null))])
    error_message = "NetBox-managed network interfaces must set prefix and one of vnet, network_name, or bridge."
  }

  validation {
    condition     = length([for nic in var.network_interfaces : nic.name if try(nic.ansible_primary, false)]) <= 1
    error_message = "At most one network interface can set ansible_primary = true."
  }
}
