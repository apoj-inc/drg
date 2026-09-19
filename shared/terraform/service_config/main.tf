locals {
  platform = yamldecode(file(var.platform_config_file))
  service  = yamldecode(file(var.service_config_file))

  existing_guests_by_name = merge(
    {
      for vm in data.proxmox_virtual_environment_vms.all.vms :
      vm.name => vm.vm_id
      if contains(local.instance_names, vm.name)
    },
    {
      for container in data.proxmox_virtual_environment_containers.all.containers :
      container.name => container.vm_id
      if contains(local.instance_names, container.name)
    },
  )
  occupied_vm_ids = toset([
    for vm in data.proxmox_virtual_environment_vms.all.vms : vm.vm_id
  ])
  occupied_container_ids = toset([
    for container in data.proxmox_virtual_environment_containers.all.containers : container.vm_id
  ])
  occupied_guest_ids = setunion(local.occupied_vm_ids, local.occupied_container_ids)

  selected_cluster   = try(local.platform.clusters[var.cluster], null)
  selected_contour   = try(local.platform.rollout_contours[var.contour], null)
  rollout_by_contour = try(local.service.rollout.by_contour, false)
  target_nodes       = try(local.service.placement.clusters[var.cluster].nodes, {})
  vm_id_start        = try(local.service.instance.vm_id_ranges[var.cluster][var.contour].start, null)

  replicas_by_node = {
    for node_name, node in local.target_nodes :
    node_name => tonumber(try(node.replicas_by_contour[var.contour], 0))
    if tonumber(try(node.replicas_by_contour[var.contour], 0)) > 0
  }

  replica_nodes = flatten([
    for node_name, replicas in local.replicas_by_node : [
      for _ in range(replicas) : node_name
    ]
  ])

  contour_instance_names = [
    for ordinal, node_name in local.replica_nodes : format(
      "%s-%s-%s-%02d",
      local.service.service.name,
      var.cluster,
      lower(var.contour),
      ordinal + 1,
    )
  ]

  standard_instance_names = [
    for ordinal, node_name in local.replica_nodes : (
      ordinal == 0 ? local.service.service.name : format("%s-%02d", local.service.service.name, ordinal + 1)
    )
  ]

  instance_names = local.rollout_by_contour ? local.contour_instance_names : local.standard_instance_names

  new_instance_names = [
    for name in local.instance_names : name
    if !contains(keys(local.existing_guests_by_name), name)
  ]

  candidate_vm_ids = range(
    local.vm_id_start,
    local.vm_id_start + length(local.occupied_guest_ids) + length(local.instance_names) + 100,
  )

  available_vm_ids = [
    for vm_id in local.candidate_vm_ids : vm_id
    if !contains(local.occupied_guest_ids, vm_id)
  ]

  vm_ids_by_name = merge(
    local.existing_guests_by_name,
    zipmap(local.new_instance_names, slice(local.available_vm_ids, 0, length(local.new_instance_names))),
  )

  instances = {
    for ordinal, node_name in local.replica_nodes :
    local.instance_names[ordinal] => {
      cluster = var.cluster
      contour = var.contour
      node    = node_name
      ordinal = ordinal + 1
      vm_id   = local.vm_ids_by_name[local.instance_names[ordinal]]
    }
  }

  instance_network_interfaces = {
    for instance_name, instance in local.instances : instance_name => [
      for nic in local.service.network.interfaces : {
        name                    = nic.name
        bridge                  = try(nic.bridge, local.selected_cluster.networks[nic.network].bridge, null)
        address                 = try(nic.address_by_contour[var.contour], nic.address, null)
        gateway                 = try(nic.gateway, local.selected_cluster.networks[nic.network].gateway, null)
        discover_gateway        = try(nic.discover_gateway, true)
        mtu                     = try(nic.mtu, local.selected_cluster.networks[nic.network].mtu, null)
        mac_address             = try(nic.mac_address, null)
        mode                    = try(nic.mode, "static")
        prefix                  = try(nic.prefix, local.selected_cluster.networks[nic.network].prefix, null)
        vrf                     = try(nic.vrf, local.selected_cluster.networks[nic.network].vrf, null)
        vnet                    = try(nic.vnet, local.selected_cluster.networks[nic.network].vnet, null)
        network_name            = try(nic.network_name, null)
        role                    = try(nic.role, null)
        dns_name                = try(nic.dns_name, format(replace(replace(nic.dns_name_pattern, "%%{cluster}", var.cluster), "%%{contour}", lower(var.contour)), instance.ordinal), null)
        dns_zone                = try(nic.dns_zone, local.selected_cluster.networks[nic.network].dns_zone, null)
        dns_ttl                 = try(nic.dns_ttl, try(local.platform.dns.default_ttl, null))
        dns_proxy               = try(nic.dns_proxy, false)
        dns_proxy_address       = try(nic.dns_proxy_address, null)
        dns_proxy_target        = try(nic.dns_proxy_target, null)
        dns_disable_ptr         = try(nic.dns_disable_ptr, null)
        disable_ptr             = try(nic.disable_ptr, null)
        dns_reverse_zone        = try(nic.dns_reverse_zone, local.selected_cluster.networks[nic.network].dns_reverse_zone, null)
        dns_reverse_nameservers = try(nic.dns_reverse_nameservers, local.selected_cluster.networks[nic.network].dns_reverse_nameservers, [])
        dns_reverse_soa_mname   = try(nic.dns_reverse_soa_mname, local.selected_cluster.networks[nic.network].dns_reverse_soa_mname, null)
        dns_reverse_soa_rname   = try(nic.dns_reverse_soa_rname, local.selected_cluster.networks[nic.network].dns_reverse_soa_rname, null)
        ansible_primary         = try(nic.ansible_primary, false)
      }
    ]
  }
}

data "proxmox_virtual_environment_vms" "all" {}

data "proxmox_virtual_environment_containers" "all" {}

check "platform_cluster_exists" {
  assert {
    condition     = local.selected_cluster != null
    error_message = "Cluster ${var.cluster} is not declared in platform.yaml."
  }
}

check "platform_contour_exists" {
  assert {
    condition     = local.selected_contour != null
    error_message = "Rollout contour ${var.contour} is not declared in platform.yaml."
  }
}

check "service_deploys_to_cluster" {
  assert {
    condition     = length(local.target_nodes) > 0
    error_message = "Service config has no placement for cluster ${var.cluster}."
  }
}

check "deployment_nodes_exist" {
  assert {
    condition = alltrue([
      for node_name in keys(local.target_nodes) :
      can(local.selected_cluster.nodes[node_name])
    ])
    error_message = "A deployment node is not declared in platform.yaml for cluster ${var.cluster}."
  }
}

check "contour_has_replicas" {
  assert {
    condition     = length(local.instances) > 0
    error_message = "Service config has no replicas for contour ${var.contour} in cluster ${var.cluster}."
  }
}

check "contour_has_vm_id_range" {
  assert {
    condition     = local.vm_id_start != null
    error_message = "Service config has no vm_id_ranges entry for cluster ${var.cluster}, contour ${var.contour}."
  }
}

check "vm_id_capacity" {
  assert {
    condition     = length(local.available_vm_ids) >= length(local.new_instance_names)
    error_message = "Not enough free VM IDs from the configured start for ${var.cluster}/${var.contour}."
  }
}

check "service_startup_order" {
  assert {
    condition = try(
      tonumber(local.service.startup.order) >= 0,
      false,
    )
    error_message = "Service config must define startup.order as a non-negative number."
  }
}

check "dns_routing_contract" {
  assert {
    condition = (
      can(local.service.dns.canonical_name) &&
      can(local.service.dns.zone) &&
      can(local.service.dns.routing.mode) &&
      contains(["direct", "ingress", "static"], local.service.dns.routing.mode) &&
      (
        local.service.dns.routing.mode != "ingress" ||
        try(local.service.dns.routing.ingress_service, null) == "traefik"
      ) &&
      (
        local.service.dns.routing.mode != "direct" ||
        contains(["A", "AAAA", "dual-stack"], try(local.service.dns.routing.direct.record_type, "A"))
      ) &&
      (
        local.service.dns.routing.mode != "static" ||
        contains(["A", "AAAA"], try(local.service.dns.routing.static.record_type, ""))
      ) &&
      (
        local.service.dns.routing.mode != "static" ||
        can(cidrhost(
          format(
            "%s/%s",
            try(local.service.dns.routing.static.address, ""),
            try(local.service.dns.routing.static.record_type, "") == "A" ? "32" : "128",
          ),
          0,
        ))
      )
    )
    error_message = "dns requires canonical_name and routing.mode (direct, ingress, or static); ingress must use Traefik, direct record_type must be A, AAAA, or dual-stack, and static mode must define an A/AAAA address."
  }
}
