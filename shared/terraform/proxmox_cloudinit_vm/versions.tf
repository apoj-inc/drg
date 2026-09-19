terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.107"
    }

    external = {
      source  = "hashicorp/external"
      version = "~> 2.4"
    }
  }
}
