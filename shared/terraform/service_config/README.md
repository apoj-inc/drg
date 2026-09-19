# Service configuration module

This module reads a non-secret platform YAML file and a non-secret service YAML file. It selects one `cluster` and one rollout `contour`, validates the selection, and outputs a stable `instances` map.

Use one Terraform state for every `service × cluster × contour` deployment unit. Do not use `-target` to emulate contour rollout.

```hcl
module "config" {
  source = "../../../shared/terraform/service_config"

  platform_config_file = abspath("${path.module}/../../../environments/example/platform.yaml")
  service_config_file  = abspath("${path.module}/../config.yaml")
  cluster              = var.deployment_cluster
  contour              = var.deployment_contour
}

module "instance" {
  for_each = module.config.instances

  source    = "../../../shared/terraform/proxmox_lxc_container"
  hostname  = each.key
  node_name = each.value.node
}
```

The generated name includes the cluster and contour to remain globally unique in NetBox. Service YAML must explicitly define the requested deployment contour on at least one node.

Contour-by-contour CI rollout is opt-in. Add the following to a service's `config.yaml` only when the service must be deployed in ordered contours:

```yaml
rollout:
  by_contour: true
```

Without this setting, the pipeline creates one service job and Terraform uses its default deployment cluster and contour.
The first (and normally only) instance is named exactly after the service, for example `dockerhub`; its hostname does not include the cluster, contour, or ordinal.

`instance_network_interfaces` resolves every logical `network` in service YAML against the selected cluster and renders `dns_name_pattern` for every replica. Pass the value matching `each.key` to the LXC/VM module; its existing NetBox IPAM flow creates the individual replica DNS record.

LXC/VM resolvers use `container.dns_servers` when set in a service `config.yaml`.
Otherwise they inherit the platform-wide `dns.servers` default.

The common `dns.canonical_name` is reconciled only after Ansible has passed the
service health check. `direct` publishes one A/AAAA RRset from NetBox VMs tagged
`dns-eligible`; it never emits multiple CNAME records. `ingress` resolves its
single CNAME or A/AAAA target from `TRAEFIK_DNS_TARGETS`, supplied by
`defaults.traefik_dns_targets` in `shared/templates/pipeline-services.yml`.
`static` publishes the explicitly configured A or AAAA address and is intended
for service VIPs that are not represented by a single NetBox VM interface.
