#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import ipaddress
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

import pynetbox


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def read_query():
    env_query = os.environ.get("NETBOX_VM_IPAM_QUERY")
    if env_query:
        try:
            return json.loads(env_query)
        except json.JSONDecodeError as exc:
            fail(f"Invalid NETBOX_VM_IPAM_QUERY JSON: {exc}")
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail(f"Invalid external data query JSON: {exc}")


def bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def load_json_field(query, key, default):
    value = query.get(key)
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON in query key {key}: {exc}")


def host(address):
    return str(ipaddress.ip_interface(address).ip)


def ip_record_type(address):
    return "A" if ipaddress.ip_interface(address).ip.version == 4 else "AAAA"


def first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def sdn_id(item, fallback):
    custom_fields = item.get("custom_fields") or {}
    value = clean(custom_fields.get("proxmox_sdn_id"))
    if value:
        return value
    return "".join(ch for ch in str(fallback).lower() if ch.isalnum())


def slug(value):
    result = []
    last_dash = False
    for char in str(value).lower():
        if char.isalnum():
            result.append(char)
            last_dash = False
        elif not last_dash:
            result.append("-")
            last_dash = True
    return "".join(result).strip("-")


def unique(items):
    result = []
    for item in items:
        item = clean(item)
        if item and item not in result:
            result.append(item)
    return result


def dns_zone_name(value):
    value = clean(value)
    if not value:
        return None
    return value.rstrip(".").lower()


def dns_relative_name(name, zone):
    name = clean(name)
    zone = dns_zone_name(zone)
    if not name or not zone:
        return None

    name = name.rstrip(".").lower()
    suffix = f".{zone}"
    if name == zone:
        return "@"
    if name.endswith(suffix):
        return name[: -len(suffix)] or "@"
    return name


def dns_absolute_name(name, zone):
    name = clean(name)
    zone = dns_zone_name(zone)
    if not name:
        return None
    name = name.rstrip(".").lower()
    if not zone:
        return name
    return f"{dns_relative_name(name, zone)}.{zone}"


class RestNetBox:
    def __init__(self, url, token, validate_certs):
        self.url = url.rstrip("/")
        self.context = None if validate_certs else ssl._create_unverified_context()
        self.api = pynetbox.api(self.url, token=token)
        self.api.http_session.verify = validate_certs
        self.headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def endpoint(self, path):
        endpoints = {
            "/api/extras/tags/": self.api.extras.tags,
            "/api/ipam/vrfs/": self.api.ipam.vrfs,
            "/api/ipam/prefixes/": self.api.ipam.prefixes,
            "/api/ipam/ip-addresses/": self.api.ipam.ip_addresses,
            "/api/virtualization/clusters/": self.api.virtualization.clusters,
            "/api/virtualization/virtual-machines/": self.api.virtualization.virtual_machines,
            "/api/virtualization/interfaces/": self.api.virtualization.interfaces,
            "/api/vpn/l2vpns/": self.api.vpn.l2vpns,
        }
        return endpoints.get(path)

    @staticmethod
    def serialize(record):
        return record.serialize() if hasattr(record, "serialize") else record

    def request(self, method, path, payload=None, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}{path}"
        if query:
            url = f"{url}?{query}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=90) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            fail(f"NetBox {method} {path} failed with HTTP {exc.code}: {body}")
        except urllib.error.URLError as exc:
            fail(f"NetBox {method} {path} failed: {exc}")

    def get_json(self, path, params=None):
        endpoint = self.endpoint(path)
        if endpoint:
            record_id = clean(path.rstrip("/").split("/")[-1])
            if record_id and record_id.isdigit():
                return self.serialize(endpoint.get(int(record_id)))

        endpoint_path, _, record_id = path.rstrip("/").rpartition("/")
        endpoint = self.endpoint(f"{endpoint_path}/")
        if endpoint and record_id.isdigit():
            return self.serialize(endpoint.get(int(record_id)))
        return self.request("GET", path, params=params)

    def list(self, path, params=None):
        endpoint = self.endpoint(path)
        if endpoint:
            try:
                return [self.serialize(item) for item in endpoint.filter(**(params or {}))]
            except Exception as exc:
                fail(f"NetBox GET {path} failed: {exc}")

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
        if len(items) > 1:
            fail(f"Expected at most one NetBox object at {path}, found {len(items)}")
        return items[0] if items else None

    def post(self, path, payload):
        endpoint = self.endpoint(path)
        if endpoint:
            try:
                return self.serialize(endpoint.create(**payload))
            except Exception as exc:
                fail(f"NetBox POST {path} failed: {exc}")
        return self.request("POST", path, payload=payload)

    def patch(self, path, payload):
        endpoint_path, _, record_id = path.rstrip("/").rpartition("/")
        endpoint = self.endpoint(f"{endpoint_path}/")
        if endpoint and record_id.isdigit():
            try:
                record = endpoint.get(int(record_id))
                record.update(payload)
                return self.serialize(record)
            except Exception as exc:
                fail(f"NetBox PATCH {path} failed: {exc}")
        return self.request("PATCH", path, payload=payload)


class NetBoxVmIpam:
    def __init__(self, query):
        missing = [key for key in ["netbox_url", "netbox_token"] if not query.get(key)]
        if missing:
            fail(f"Missing query keys: {', '.join(missing)}")
        self.client = RestNetBox(
            query["netbox_url"],
            query["netbox_token"],
            bool_value(query.get("netbox_validate_certs", "true")),
        )
        self.hostname = query.get("hostname")
        self.node_name = query.get("node_name")
        self.vm_id = int(query.get("vm_id"))
        self.site = clean(query.get("netbox_site"))
        self.cluster_name = clean(query.get("netbox_cluster_name"))
        self.dns_records_enabled = bool_value(query.get("netbox_dns_records_enabled", "true"))
        self.tags = unique(load_json_field(query, "tags", []) + ["opentofu", "terraform-managed"])
        self._tag_ids = {}
        self.interfaces = load_json_field(query, "interfaces", [])

    def tag_ids(self, tags):
        result = []
        for tag in unique(tags):
            if tag not in self._tag_ids:
                item = self.client.one("/api/extras/tags/", {"slug": tag}) or self.client.one(
                    "/api/extras/tags/", {"name": tag}
                )
                if not item:
                    item = self.client.post(
                        "/api/extras/tags/",
                        {
                            "name": tag,
                            "slug": slug(tag),
                            "color": "9e9e9e",
                            "description": "Created by Terraform/OpenTofu VM registration",
                        },
                    )
                self._tag_ids[tag] = item["id"]
            result.append(self._tag_ids[tag])
        return result

    def vrf_id(self, name):
        name = clean(name)
        if not name:
            return None
        vrf = self.client.one("/api/ipam/vrfs/", {"name": name})
        if not vrf:
            fail(f"NetBox VRF {name!r} was not found")
        return vrf["id"]

    def prefix(self, cidr, vrf_name=None):
        cidr = clean(cidr)
        if not cidr:
            fail("NetBox-managed interfaces must set prefix")
        params = {"prefix": cidr}
        vrf = self.vrf_id(vrf_name)
        if vrf:
            params["vrf_id"] = vrf
        prefix = self.client.one("/api/ipam/prefixes/", params)
        if not prefix:
            fail(f"NetBox prefix {cidr!r} was not found")
        return prefix

    def discover_bridge(self, nic):
        if clean(nic.get("bridge")):
            return clean(nic.get("bridge"))

        vnet = clean(nic.get("vnet")) or clean(nic.get("network_name"))
        if not vnet:
            fail(f"NetBox-managed interface {nic.get('name')} must set vnet, network_name, or bridge")

        lookups = [
            {"name": vnet, "limit": 10},
            {"slug": slug(vnet), "limit": 10},
            {"cf_proxmox_vnet": vnet, "limit": 10},
            {"cf_proxmox_sdn_id": vnet, "limit": 10},
        ]
        for params in lookups:
            for item in self.client.list("/api/vpn/l2vpns/", params):
                custom_fields = item.get("custom_fields") or {}
                if (
                    clean(custom_fields.get("proxmox_vnet")) == vnet
                    or clean(custom_fields.get("proxmox_sdn_id")) == vnet
                    or clean(item.get("name")) == vnet
                    or clean(item.get("slug")) == slug(vnet)
                ):
                    return sdn_id(item, vnet)

        fail(f"Could not discover Proxmox bridge/VNet ID for NetBox VNet {vnet!r}")

    def find_ip(self, address, vrf_name=None):
        params = {"address": address}
        vrf = self.vrf_id(vrf_name)
        if vrf:
            params["vrf_id"] = vrf
        return self.client.one("/api/ipam/ip-addresses/", params)

    def existing_assigned_ip(self, nic, prefix):
        vm = self.client.one(
            "/api/virtualization/virtual-machines/",
            {"cf_proxmox_vmid": self.vm_id},
        )
        if not vm:
            cluster = self.get_cluster()
            vm = self.client.one(
                "/api/virtualization/virtual-machines/",
                {"name": self.hostname, "cluster_id": cluster["id"]},
            )
        if not vm:
            return None

        interface = self.client.one(
            "/api/virtualization/interfaces/",
            {"virtual_machine_id": vm["id"], "name": nic.get("name")},
        )
        if not interface:
            return None

        network = ipaddress.ip_network(prefix["prefix"], strict=False)
        assigned_ips = self.client.list(
            "/api/ipam/ip-addresses/",
            {
                "assigned_object_type": "virtualization.vminterface",
                "assigned_object_id": interface["id"],
            },
        )
        matches = []
        for item in assigned_ips:
            try:
                item_ip = ipaddress.ip_interface(item["address"])
            except (KeyError, ValueError):
                continue
            if item_ip.ip in network:
                matches.append(item)
        if len(matches) > 1:
            fail(f"Multiple assigned NetBox IPs match VMID {self.vm_id} interface {nic['name']}")
        return matches[0] if matches else None

    def reserved_ip(self, nic):
        prefix = self.prefix(nic.get("prefix"), nic.get("vrf"))
        network = ipaddress.ip_network(prefix["prefix"], strict=False)
        vrf = self.vrf_id(nic.get("vrf"))
        params = {"cf_proxmox_vmid": self.vm_id, "cf_proxmox_interface": nic["name"]}
        if vrf:
            params["vrf_id"] = vrf

        matches = []
        for item in self.client.list("/api/ipam/ip-addresses/", params):
            try:
                item_ip = ipaddress.ip_interface(item["address"])
            except (KeyError, ValueError):
                continue
            if item_ip.ip not in network:
                continue
            if clean((item.get("status") or {}).get("value") if isinstance(item.get("status"), dict) else item.get("status")) != "reserved":
                continue
            if item.get("assigned_object") or item.get("assigned_object_id"):
                continue
            matches.append(item)

        if len(matches) > 1:
            fail(f"Multiple reserved NetBox IPs match VMID {self.vm_id} interface {nic['name']}")
        return matches[0] if matches else None

    def discover_gateway(self, nic):
        if not bool_value(nic.get("discover_gateway", True)):
            return None
        if clean(nic.get("gateway")):
            return clean(nic.get("gateway"))

        vnet = clean(nic.get("vnet")) or clean(nic.get("network_name")) or clean(nic.get("bridge"))
        prefix = self.prefix(nic.get("prefix"), nic.get("vrf"))
        network = ipaddress.ip_network(prefix["prefix"], strict=False)
        vrf = self.vrf_id(nic.get("vrf"))
        params = {}
        if vrf:
            params["vrf_id"] = vrf

        candidates = []
        for item in self.client.list("/api/ipam/ip-addresses/", params):
            try:
                item_ip = ipaddress.ip_interface(item["address"])
            except (KeyError, ValueError):
                continue
            if item_ip.ip not in network:
                continue
            custom_fields = item.get("custom_fields") or {}
            item_vnet = clean(custom_fields.get("proxmox_vnet"))
            if vnet and item_vnet and item_vnet != vnet:
                continue
            if vnet and not item_vnet:
                continue
            if not bool_value(custom_fields.get("proxmox_gateway", False)):
                continue
            if item.get("assigned_object") or item.get("assigned_object_id"):
                continue
            candidates.append(item)

        if len(candidates) != 1:
            fail(
                f"Expected exactly one Proxmox gateway for interface {nic.get('name')} "
                f"in NetBox prefix {prefix['prefix']} and vnet {vnet!r}, found {len(candidates)}"
            )
        return host(candidates[0]["address"])

    def ip_payload(self, nic, address, status):
        mode = clean(nic.get("mode")) or "static"
        source_tag = "netbox-managed" if mode == "netbox" else "terraform-observed"
        payload = {
            "address": address,
            "status": status,
            "description": (
                f"{self.hostname} {nic['name']} "
                f"{clean(nic.get('mode')) or 'static'} "
                f"{clean(nic.get('bridge')) or clean(nic.get('vnet')) or ''}".strip()
            ),
            "tags": self.tag_ids(self.tags + [source_tag]),
            "custom_fields": {
                "proxmox_vmid": self.vm_id,
                "proxmox_interface": nic["name"],
            },
        }
        dns_name = clean(nic.get("dns_name"))
        dns_zone = dns_zone_name(nic.get("dns_zone"))
        if dns_name:
            payload["dns_name"] = dns_absolute_name(dns_name, dns_zone)
        vrf = self.vrf_id(nic.get("vrf"))
        if vrf:
            payload["vrf"] = vrf
        return payload

    def reserve_next_ip(self, nic):
        prefix = self.prefix(nic.get("prefix"), nic.get("vrf"))
        existing = self.existing_assigned_ip(nic, prefix)
        if existing:
            return existing["address"]
        payload = self.ip_payload(nic, "", "reserved")
        payload.pop("address", None)
        payload.pop("vrf", None)
        return self.client.post(f"/api/ipam/prefixes/{prefix['id']}/available-ips/", payload)["address"]

    def prepare(self):
        primary_count = len([nic for nic in self.interfaces if bool_value(nic.get("ansible_primary", False))])
        if primary_count > 1:
            fail("Only one interface can set ansible_primary=true")

        prepared = []
        for nic in self.interfaces:
            nic = dict(nic)
            mode = clean(nic.get("mode")) or "static"
            nic["mode"] = mode
            if mode == "netbox":
                nic["bridge"] = self.discover_bridge(nic)
                if nic.get("address"):
                    fail(f"NetBox-managed interface {nic.get('name')} must not set address; reserve it in NetBox by VMID and interface")
                existing = self.existing_assigned_ip(nic, self.prefix(nic.get("prefix"), nic.get("vrf")))
                reserved = self.reserved_ip(nic)
                if existing and reserved:
                    fail(f"Both assigned and reserved NetBox IPs match VMID {self.vm_id} interface {nic['name']}")
                nic["address"] = (existing or reserved or {"address": self.reserve_next_ip(nic)})["address"]
                nic["gateway"] = self.discover_gateway(nic)
            elif not nic.get("address"):
                fail(f"Static interface {nic.get('name')} must set address")
            prepared.append(nic)

        print(json.dumps({"interfaces": json.dumps(prepared, sort_keys=True)}))

    def get_cluster(self):
        if not self.cluster_name:
            fail("netbox_cluster_name is required for VM registration")
        cluster = self.client.one("/api/virtualization/clusters/", {"name": self.cluster_name})
        if not cluster:
            fail(f"NetBox cluster {self.cluster_name!r} was not found")
        return cluster

    def ensure_vm(self):
        cluster = self.get_cluster()
        payload = {
            "name": self.hostname,
            "cluster": cluster["id"],
            "status": "active",
            "description": f"Proxmox LXC {self.vm_id} on {self.node_name}",
            "comments": f"Managed by Terraform/OpenTofu. Proxmox node: {self.node_name}. VMID: {self.vm_id}.",
            "tags": self.tag_ids(self.tags),
            "custom_fields": {"proxmox_vmid": self.vm_id},
        }

        vm = self.client.one("/api/virtualization/virtual-machines/", {"cf_proxmox_vmid": self.vm_id})
        if not vm:
            vm = self.client.one("/api/virtualization/virtual-machines/", {"name": self.hostname, "cluster_id": cluster["id"]})
        if vm:
            return self.client.patch(f"/api/virtualization/virtual-machines/{vm['id']}/", payload)
        return self.client.post("/api/virtualization/virtual-machines/", payload)

    def ensure_interface(self, vm, nic):
        name = nic["name"]
        payload = {
            "virtual_machine": vm["id"],
            "name": name,
            "enabled": True,
            "description": (
                f"bridge={clean(nic.get('bridge')) or ''}; "
                f"vnet={clean(nic.get('vnet')) or clean(nic.get('network_name')) or ''}; "
                f"mode={clean(nic.get('mode')) or 'static'}; "
                f"role={clean(nic.get('role')) or ''}; "
                f"ansible_primary={str(bool_value(nic.get('ansible_primary', False))).lower()}"
            ),
            "tags": self.tag_ids(self.tags),
        }
        if clean(nic.get("mac_address")):
            payload["mac_address"] = clean(nic.get("mac_address"))
        interface = self.client.one(
            "/api/virtualization/interfaces/",
            {"virtual_machine_id": vm["id"], "name": name},
        )
        if interface:
            return self.client.patch(f"/api/virtualization/interfaces/{interface['id']}/", payload)
        return self.client.post("/api/virtualization/interfaces/", payload)

    def ensure_assigned_ip(self, nic, interface):
        address = nic.get("address")
        if not address:
            return None
        status = "active"
        existing = self.find_ip(address, nic.get("vrf"))
        payload = self.ip_payload(nic, address, status)
        payload["assigned_object_type"] = "virtualization.vminterface"
        payload["assigned_object_id"] = interface["id"]
        payload["description"] = (
            f"{self.hostname} {nic['name']} "
            f"source={'netbox-managed' if nic.get('mode') == 'netbox' else 'terraform-observed/static'}; "
            f"bridge={clean(nic.get('bridge')) or ''}; "
            f"vnet={clean(nic.get('vnet')) or clean(nic.get('network_name')) or ''}"
        )

        if existing:
            assigned_type = existing.get("assigned_object_type")
            assigned_id = existing.get("assigned_object_id") or (existing.get("assigned_object") or {}).get("id")
            if assigned_type and assigned_id and int(assigned_id) != int(interface["id"]):
                fail(f"IP {address} is assigned to another NetBox object; drift/conflict detected")
            return self.client.patch(f"/api/ipam/ip-addresses/{existing['id']}/", payload)
        return self.client.post("/api/ipam/ip-addresses/", payload)

    def dns_zone(self, zone_name):
        zone_name = dns_zone_name(zone_name)
        if not zone_name:
            return None
        zone = self.client.one("/api/plugins/netbox-dns/zones/", {"name": zone_name})
        if not zone:
            fail(f"NetBox DNS zone {zone_name!r} was not found")
        return zone

    def ensure_dns_nameserver(self, name):
        name = dns_absolute_name(name, None)
        if not name:
            fail("Reverse DNS nameservers must contain non-empty FQDNs")

        nameserver = self.client.one("/api/plugins/netbox-dns/nameservers/", {"name": name})
        if nameserver:
            return nameserver

        return self.client.post(
            "/api/plugins/netbox-dns/nameservers/",
            {
                "name": name,
                "description": "Managed by Terraform/OpenTofu reverse DNS configuration",
                "tags": self.tag_ids(self.tags + ["dns"]),
            },
        )

    def ensure_reverse_dns_zone(self, nic, address):
        if not address:
            return None

        disable_ptr = bool_value(first_present(nic.get("dns_disable_ptr"), nic.get("disable_ptr"), False))
        zone_name = dns_zone_name(nic.get("dns_reverse_zone"))
        nameserver_names = unique(nic.get("dns_reverse_nameservers") or [])
        if disable_ptr or not zone_name:
            return None
        if not nameserver_names:
            fail(
                f"Interface {nic.get('name')} enables PTR automation but does not define "
                "dns_reverse_nameservers"
            )

        nameservers = [self.ensure_dns_nameserver(name) for name in nameserver_names]
        soa_mname_name = dns_absolute_name(
            nic.get("dns_reverse_soa_mname") or nameserver_names[0], None
        )
        soa_mname = next(
            (item for item in nameservers if dns_zone_name(item.get("name")) == dns_zone_name(soa_mname_name)),
            None,
        )
        if not soa_mname:
            soa_mname = self.ensure_dns_nameserver(soa_mname_name)
            nameservers.append(soa_mname)

        ttl = int(nic.get("dns_ttl") or 60)
        marker = f"Terraform-managed reverse DNS zone {zone_name}"
        payload = {
            "name": zone_name,
            "status": "active",
            "description": marker,
            "nameservers": [item["id"] for item in nameservers],
            "default_ttl": ttl,
            "soa_ttl": ttl,
            "soa_mname": soa_mname["id"],
            "soa_rname": clean(nic.get("dns_reverse_soa_rname")) or "hostmaster.example.test",
            "soa_serial_auto": True,
            "soa_refresh": 43200,
            "soa_retry": 7200,
            "soa_expire": 2419200,
            "soa_minimum": ttl,
            "tags": self.tag_ids(self.tags + ["dns"]),
        }

        zone = self.client.one("/api/plugins/netbox-dns/zones/", {"name": zone_name})
        if zone:
            # Do not rewrite zones managed outside this automation. The plugin will
            # still generate PTRs as soon as the matching reverse zone exists.
            if clean(zone.get("description")) == marker:
                return self.client.patch(f"/api/plugins/netbox-dns/zones/{zone['id']}/", payload)
            return zone
        return self.client.post("/api/plugins/netbox-dns/zones/", payload)

    def ensure_dns_record(self, nic, ip):
        if not self.dns_records_enabled:
            return None

        dns_name = clean(nic.get("dns_name"))
        dns_zone = clean(nic.get("dns_zone"))
        if not dns_name or not dns_zone or not ip:
            return None

        zone = self.dns_zone(dns_zone)
        use_proxy = bool_value(nic.get("dns_proxy", False))
        if use_proxy:
            target = clean(nic.get("dns_proxy_target"))
            if not target:
                fail(f"Interface {nic.get('name')} sets dns_proxy=true but dns_proxy_target is empty")
            value = f"{dns_absolute_name(target, zone['name'])}."
            record_type = "CNAME"
        else:
            address = ip["address"] if isinstance(ip, dict) else nic.get("address")
            value = host(address)
            record_type = ip_record_type(address)
            self.ensure_reverse_dns_zone(nic, address)
        record_name = dns_relative_name(dns_name, zone["name"])
        marker = f"Terraform-managed DNS for {self.hostname} {nic['name']}"
        payload = {
            "zone": zone["id"],
            "type": record_type,
            "name": record_name,
            "value": value,
            "status": "active",
            "description": marker,
            "tags": self.tag_ids(self.tags + ["dns"]),
        }
        if record_type in {"A", "AAAA"}:
            payload["disable_ptr"] = bool_value(first_present(nic.get("dns_disable_ptr"), nic.get("disable_ptr"), False))
            payload["ip_address"] = value
            if isinstance(ip, dict):
                payload["ipam_ip_address"] = ip["id"]
        if nic.get("dns_ttl") is not None:
            payload["ttl"] = int(nic["dns_ttl"])

        candidates = self.client.list(
            "/api/plugins/netbox-dns/records/",
            {"zone_id": zone["id"], "name": record_name},
        )
        matching = [record for record in candidates if clean(record.get("description")) == marker]
        same_type = [record for record in candidates if record.get("type") == record_type]
        if not matching and len(same_type) == 1:
            matching = same_type
        if len(matching) > 1:
            fail(
                f"Multiple NetBox DNS {record_type} records match "
                f"{record_name}.{zone['name']} for {self.hostname} {nic['name']}"
            )
        if matching:
            return self.client.patch(f"/api/plugins/netbox-dns/records/{matching[0]['id']}/", payload)
        return self.client.post("/api/plugins/netbox-dns/records/", payload)

    def finalize(self):
        primary = [nic for nic in self.interfaces if bool_value(nic.get("ansible_primary", False))]
        if len(primary) != 1:
            fail("Exactly one interface must set ansible_primary=true for NetBox dynamic inventory")

        vm = self.ensure_vm()
        primary_ip = None
        for nic in self.interfaces:
            interface = self.ensure_interface(vm, nic)
            ip = self.ensure_assigned_ip(nic, interface)
            self.ensure_dns_record(nic, ip)
            if bool_value(nic.get("ansible_primary", False)):
                primary_ip = ip

        if not primary_ip:
            fail("Primary Ansible interface did not produce an IP address")
        vm = self.client.patch(f"/api/virtualization/virtual-machines/{vm['id']}/", {"primary_ip4": primary_ip["id"]})
        print(json.dumps({"vm_id": str(vm["id"]), "primary_ip4": host(primary_ip["address"])}))


def main():
    query = read_query()
    action = query.get("action")
    ipam = NetBoxVmIpam(query)
    if action == "prepare":
        ipam.prepare()
    elif action == "finalize":
        ipam.finalize()
    else:
        fail("action must be prepare or finalize")


if __name__ == "__main__":
    main()
