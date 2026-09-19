#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import ipaddress
import json
import ssl
import sys
import urllib.parse
import urllib.request


def fail(message):
    print(json.dumps({"error": message}))
    sys.exit(0)


def read_query():
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail(f"Invalid external data query JSON: {exc}")


def bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def custom_fields(item):
    return item.get("custom_fields") or {}


def tags(item):
    result = []
    for tag in item.get("tags") or []:
        if isinstance(tag, dict):
            result.append(tag.get("slug") or tag.get("name"))
        else:
            result.append(str(tag))
    return {tag for tag in result if tag}


def has_tags(item, required):
    return set(required).issubset(tags(item))


def sdn_id(item, fallback):
    value = custom_fields(item).get("proxmox_sdn_id")
    if value:
        return str(value)
    return "".join(ch for ch in fallback.lower() if ch.isalnum())


def short_sdn_id(value):
    result = "".join(ch for ch in str(value).lower() if ch.isalnum())
    return result[:8]


def first_prefix(prefixes, required_tags, role=None):
    for prefix in prefixes:
        description = str(prefix.get("description") or "").lower()
        underlay_role = str(custom_fields(prefix).get("proxmox_underlay_role") or "").lower()
        role_matches = role is None or role == underlay_role or role in description
        if has_tags(prefix, required_tags) and role_matches:
            return prefix
    return None


def address_host(address):
    return str(ipaddress.ip_interface(address).ip)


def gateway_host(address):
    return str(ipaddress.ip_interface(address).ip)


def target_name(item):
    if isinstance(item, dict):
        return item.get("name") or item.get("display") or item.get("value")
    return str(item)


def csv_list(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def first_route_target(item):
    targets = item.get("import_targets") or []
    if not targets:
        return None
    return target_name(targets[0])


class RestNetBox:
    def __init__(self, url, token, validate_certs):
        self.url = url.rstrip("/")
        self.token = token
        self.context = None if validate_certs else ssl._create_unverified_context()

    def get_json(self, path, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}{path}"
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self.token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def list(self, path, params=None):
        params = dict(params or {})
        params.setdefault("limit", 1000)
        results = []
        payload = self.get_json(path, params)
        if isinstance(payload, list):
            return payload
        results.extend(payload.get("results", []))
        next_url = payload.get("next")
        while next_url:
            parsed = urllib.parse.urlparse(next_url)
            payload = self.get_json(parsed.path, urllib.parse.parse_qs(parsed.query))
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
        return results

    def one(self, path, params=None):
        items = self.list(path, params)
        return items[0] if items else None


class NetBox:
    def __init__(self, url, token, validate_certs):
        self.client = RestNetBox(url, token, validate_certs)

    def config_context(self, name):
        return self.client.one("/api/extras/config-contexts/", {"name": name})

    def vrfs(self, tag):
        return self.client.list("/api/ipam/vrfs/", {"tag": tag})

    def prefixes(self, tag):
        return self.client.list("/api/ipam/prefixes/", {"tag": tag})

    def ip_addresses(self, tag):
        return self.client.list("/api/ipam/ip-addresses/", {"tag": tag})

    def l2vpns(self, tag):
        return self.client.list("/api/vpn/l2vpns/", {"tag": tag})


def build_model(
    netbox,
    context_name,
    platform_nodes,
    platform_fabric_prefix,
    platform_exit_nodes,
    platform_primary_exit_node,
    platform_exit_nodes_local_routing,
):
    context = netbox.config_context(context_name)
    if not context:
        fail(f"NetBox config context {context_name!r} was not found")

    data = context.get("data") or {}
    config = data.get("proxmox_sdn") or {}
    terraform = config.get("terraform") or {}
    fabric_config = config.get("fabric") or {}
    controller_defaults = config.get("controller_defaults") or {}

    object_tag = terraform.get("object_tag", "proxmox-sdn")
    managed_tag = terraform.get("managed_tag", "terraform-managed")
    evpn_tag = terraform.get("evpn_tag", "proxmox-evpn")
    underlay_tag = terraform.get("underlay_tag", "proxmox-underlay")

    vrfs = [item for item in netbox.vrfs(object_tag) if has_tags(item, [managed_tag, evpn_tag])]
    prefixes = netbox.prefixes(object_tag)
    ip_addresses = netbox.ip_addresses(object_tag)
    l2vpns = [item for item in netbox.l2vpns(object_tag) if has_tags(item, [managed_tag, evpn_tag])]

    underlay_prefixes = [item for item in prefixes if has_tags(item, [managed_tag, underlay_tag])]
    underlay_ips = [item for item in ip_addresses if has_tags(item, [managed_tag, underlay_tag])]
    fabric_ips = [
        item
        for item in underlay_ips
        if str(custom_fields(item).get("proxmox_underlay_role") or "").lower() == "fabric"
    ]
    loopback_ips = [
        item
        for item in underlay_ips
        if str(custom_fields(item).get("proxmox_underlay_role") or "").lower() == "loopback"
    ]
    gateway_ips = [item for item in ip_addresses if has_tags(item, [managed_tag, evpn_tag])]
    vnet_prefixes = [item for item in prefixes if has_tags(item, [managed_tag, evpn_tag])]

    fabric_prefix = first_prefix(underlay_prefixes, [underlay_tag], "transit")
    if not fabric_prefix:
        fabric_prefix = first_prefix(underlay_prefixes, [underlay_tag], "fabric")
    if not fabric_prefix:
        fabric_prefix = first_prefix(underlay_prefixes, [underlay_tag])
    if not fabric_prefix:
        fail("No underlay prefix tagged for Proxmox SDN fabric was found")

    fabric = {
        "enabled": True,
        "id": fabric_config.get("id") or short_sdn_id(fabric_config.get("name", "example-fabric")),
        "name": fabric_config.get("name", "pve-openfabric"),
        "protocol": fabric_config.get("protocol", "openfabric"),
        "mtu": int(fabric_config.get("mtu", 9000)),
        "ip_prefix": platform_fabric_prefix,
    }

    nodes = {}
    for node_name, node_config in platform_nodes.items():
        if not isinstance(node_config, dict):
            fail(f"Platform Proxmox node {node_name!r} must be a mapping")
        required = ["fabric_ip", "loopback", "fabric_interfaces"]
        missing = [key for key in required if not node_config.get(key)]
        if missing:
            fail(
                f"Platform Proxmox node {node_name!r} is missing: {', '.join(missing)}"
            )
        nodes[node_name] = {
            "id": node_config.get("proxmox_sdn_id") or node_name,
            "name": node_name,
            "fabric_id": fabric["id"],
            "ip": address_host(node_config["fabric_ip"]),
            "peer_ip": address_host(node_config["loopback"]),
            "interface_names": node_config["fabric_interfaces"],
        }

    if not nodes:
        fail("No Proxmox nodes are configured in the platform file")

    zones = {}
    zone_name_to_id = {}
    controller_names = set()
    for vrf in vrfs:
        cf = custom_fields(vrf)
        zone_name = cf.get("proxmox_sdn_zone") or vrf.get("name")
        zone_id = cf.get("proxmox_sdn_id") or sdn_id(vrf, zone_name)
        controller_name = cf.get("proxmox_sdn_controller") or "evpn-main"
        controller_id = "".join(ch for ch in controller_name.lower() if ch.isalnum())
        controller_names.add(controller_name)
        zones[zone_id] = {
            "id": zone_id,
            "name": zone_name,
            "controller_id": controller_id,
            "vrf_vxlan": int(cf.get("proxmox_vrf_vni")),
            "nodes": [node["id"] for node in nodes.values()],
            "exit_nodes": platform_exit_nodes,
            "exit_nodes_local_routing": platform_exit_nodes_local_routing,
            "primary_exit_node": platform_primary_exit_node,
            "mtu": fabric["mtu"],
            "ipam": "pve",
            "rt_import": first_route_target(vrf),
        }
        zone_name_to_id[zone_name] = zone_id

    controllers = {}
    for controller_name in controller_names or {"evpn-main"}:
        controller_id = "".join(ch for ch in controller_name.lower() if ch.isalnum())
        controllers[controller_id] = {
            "id": controller_id,
            "name": controller_name,
            "asn": int(controller_defaults.get("asn", 65000)),
            "fabric_id": fabric["id"],
            "peers": [node["peer_ip"] for node in nodes.values()],
        }

    vnets = {}
    vnet_name_to_id = {}
    for l2vpn in l2vpns:
        cf = custom_fields(l2vpn)
        vnet_name = cf.get("proxmox_vnet") or l2vpn.get("name")
        zone_name = cf.get("proxmox_sdn_zone")
        zone_id = zone_name_to_id.get(zone_name)
        if not zone_id:
            fail(f"VNet {vnet_name!r} references unknown zone {zone_name!r}")
        vnet_id = cf.get("proxmox_sdn_id") or sdn_id(l2vpn, vnet_name)
        vni = cf.get("proxmox_vni") or l2vpn.get("identifier")
        vnets[vnet_id] = {
            "id": vnet_id,
            "name": vnet_name,
            "alias": l2vpn.get("name"),
            "zone_id": zone_id,
            "vni": int(vni),
        }
        vnet_name_to_id[vnet_name] = vnet_id

    subnets = {}
    gateways_by_vnet = {}
    for ip in gateway_ips:
        cf = custom_fields(ip)
        vnet_name = cf.get("proxmox_vnet")
        if vnet_name:
            gateways_by_vnet[vnet_name] = gateway_host(ip["address"])

    for prefix in vnet_prefixes:
        cf = custom_fields(prefix)
        vnet_name = cf.get("proxmox_vnet")
        vnet_id = vnet_name_to_id.get(vnet_name)
        if not vnet_id:
            continue
        key = f"{vnet_id}-{prefix['prefix'].replace('/', '-')}"
        subnets[key] = {
            "id": key,
            "cidr": prefix["prefix"],
            "vnet_id": vnet_id,
            "gateway": gateways_by_vnet.get(vnet_name),
            "snat": bool_value(cf.get("proxmox_snat", False)),
        }

    return {
        "fabric": fabric,
        "nodes": nodes,
        "controllers": controllers,
        "zones": zones,
        "vnets": vnets,
        "subnets": subnets,
    }


def main():
    query = read_query()
    required = ["netbox_url", "netbox_token", "netbox_config_context_name"]
    missing = [key for key in required if not query.get(key)]
    if missing:
        fail(f"Missing external data query keys: {', '.join(missing)}")

    netbox = NetBox(
        query["netbox_url"],
        query["netbox_token"],
        bool_value(query.get("netbox_validate_certs", "true")),
    )
    try:
        platform_nodes = json.loads(query["platform_nodes"])
        platform_fabric_prefix = query["platform_fabric_prefix"]
        platform_exit_nodes = json.loads(query["platform_exit_nodes"])
        platform_primary_exit_node = query["platform_primary_exit_node"]
        platform_exit_nodes_local_routing = bool_value(query["platform_exit_nodes_local_routing"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        fail(f"Invalid platform_nodes: {exc}")
    if not isinstance(platform_nodes, dict) or not platform_nodes:
        fail("platform_nodes must be a non-empty mapping")
    if not isinstance(platform_fabric_prefix, str) or not platform_fabric_prefix:
        fail("platform_fabric_prefix must be a non-empty CIDR")
    if not isinstance(platform_exit_nodes, list) or not all(
        isinstance(name, str) and name for name in platform_exit_nodes
    ):
        fail("platform_exit_nodes must be a non-empty list of node names")
    if not isinstance(platform_primary_exit_node, str) or not platform_primary_exit_node:
        fail("platform_primary_exit_node must be a non-empty node name")
    if platform_primary_exit_node not in platform_exit_nodes:
        fail("platform_primary_exit_node must be included in platform_exit_nodes")

    model = build_model(
        netbox,
        query["netbox_config_context_name"],
        platform_nodes,
        platform_fabric_prefix,
        platform_exit_nodes,
        platform_primary_exit_node,
        platform_exit_nodes_local_routing,
    )
    print(json.dumps({"model": json.dumps(model, sort_keys=True)}))


if __name__ == "__main__":
    main()
