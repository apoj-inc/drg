output "platform" {
  description = "Decoded non-secret platform configuration."
  value       = local.platform
}

output "service" {
  description = "Decoded non-secret service configuration."
  value       = local.service
}

output "cluster" {
  description = "Selected platform cluster configuration."
  value       = local.selected_cluster
}

output "contour" {
  description = "Selected rollout contour configuration."
  value       = local.selected_contour
}

output "instances" {
  description = "Stable instance map for use with for_each; names include cluster and contour only for contour rollouts."
  value       = local.instances
}

output "instance_network_interfaces" {
  description = "Cluster-resolved network interfaces keyed by generated instance name."
  value       = local.instance_network_interfaces
}

output "dns_servers" {
  description = "DNS resolvers for service guests; service container.dns_servers overrides platform dns.servers."
  value       = try(local.service.container.dns_servers, local.platform.dns.servers, [])
}
