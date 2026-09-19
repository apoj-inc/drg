provider "proxmox" {
  endpoint  = local.cluster.proxmox.endpoint
  api_token = var.proxmox_api_token
  insecure  = try(local.cluster.proxmox.insecure, false)
}
