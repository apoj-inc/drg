locals {
  platform = yamldecode(file("${path.module}/../../../environments/example/platform.yaml"))
  service  = yamldecode(file("${path.module}/../config.yaml"))
  cluster  = local.platform.clusters[local.service.instance.deployment_cluster]
  network  = local.cluster.networks.app
}

module "llm_vm" {
  source = "../../../shared/terraform/proxmox_cloudinit_vm"

  hostname                = local.service.instance.hostname
  node_name               = local.service.instance.node_name
  vm_id                   = local.service.instance.vm_id
  template_vm_id          = local.cluster.defaults.cloudinit_template_vm_id
  description             = local.service.service.description
  startup_order           = tonumber(local.service.startup.order)
  root_password           = var.root_password
  ssh_public_keys         = var.ssh_public_keys
  cpu_cores               = local.service.compute.cpu_cores
  cpu_numa                = true
  memory_dedicated        = local.service.compute.memory_mb
  memory_min_mb           = 0
  disk_datastore_id       = local.cluster.defaults.disk_datastore
  cloud_init_datastore_id = local.cluster.defaults.cloudinit_datastore
  efi_datastore_id        = local.cluster.defaults.disk_datastore
  disk_size               = local.service.compute.disk_gb
  machine                 = "q35"
  bios                    = "ovmf"
  vga_type                = "virtio"
  pci_mappings            = local.service.gpu.pci_mappings
  numa_nodes              = local.service.compute.numa_nodes
  tags                    = local.service.service.tags

  network_interfaces = [{
    name                    = "net0"
    mode                    = "netbox"
    bridge                  = local.network.bridge
    prefix                  = local.network.prefix
    vnet                    = local.network.vnet
    network_name            = "app"
    mtu                     = try(local.network.mtu, null)
    role                    = "app"
    dns_name                = local.service.instance.hostname
    dns_zone                = local.platform.dns.domain
    dns_ttl                 = local.platform.dns.default_ttl
    dns_reverse_zone        = try(local.network.dns_reverse_zone, null)
    dns_reverse_nameservers = try(local.network.dns_reverse_nameservers, [])
    dns_reverse_soa_mname   = try(local.network.dns_reverse_soa_mname, null)
    dns_reverse_soa_rname   = try(local.network.dns_reverse_soa_rname, null)
    ansible_primary         = true
  }]

  dns_servers                = local.platform.dns.servers
  dns_domain                 = local.platform.dns.domain
  netbox_enabled             = try(local.platform.netbox.enabled, false)
  netbox_url                 = local.platform.netbox.url
  netbox_token               = var.netbox_token
  netbox_validate_certs      = local.platform.netbox.validate_certs
  netbox_dns_records_enabled = true
  netbox_site                = local.platform.netbox.site
  netbox_cluster_name        = local.platform.netbox.cluster_by_proxmox_cluster[local.service.instance.deployment_cluster]
  python_command             = var.python_command
}

check "llm_vm_sizing" {
  assert {
    condition     = sum([for node in local.service.compute.numa_nodes : node.memory]) == local.service.compute.memory_mb
    error_message = "The NUMA node memory total must equal compute.memory_mb."
  }

  assert {
    condition     = length(local.service.gpu.pci_mappings) == local.service.gpu.expected_count
    error_message = "Exactly four audited Proxmox PCI mapping names are required."
  }
}
