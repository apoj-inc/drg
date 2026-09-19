#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reconcile service canonical DNS records in NetBox DNS.

Individual replica records are created during Terraform IPAM registration.  This
tool owns only the common service record and deliberately uses a marker so it
cannot modify manually managed DNS data.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


class NetBox:
    def __init__(self, url: str, token: str, validate_certs: bool) -> None:
        self.url = url.rstrip("/")
        self.context = None if validate_certs else ssl._create_unverified_context()
        self.headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, payload=None, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}{path}" + (f"?{query}" if query else "")
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(url, data=body, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=60) as response:
                data = response.read().decode()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as exc:
            fail(f"NetBox {method} {path} returned {exc.code}: {exc.read().decode(errors='replace')}")
        except urllib.error.URLError as exc:
            fail(f"NetBox {method} {path} failed: {exc}")

    def list(self, path: str, params=None) -> list[dict]:
        params = dict(params or {})
        params.setdefault("limit", 1000)
        result = []
        payload = self.request("GET", path, params=params)
        if isinstance(payload, list):
            return payload
        result.extend(payload.get("results", []))
        while payload.get("next"):
            parsed = urllib.parse.urlparse(payload["next"])
            payload = self.request("GET", parsed.path, params=urllib.parse.parse_qs(parsed.query))
            result.extend(payload.get("results", []))
        return result

    def one(self, path: str, params=None):
        items = self.list(path, params)
        if len(items) > 1:
            fail(f"Expected one object at {path}, found {len(items)}")
        return items[0] if items else None


def slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def tag_names(record: dict) -> set[str]:
    return {str(tag.get("slug") or tag.get("name") or "") for tag in record.get("tags", [])}


def primary_address(vm: dict, record_type: str) -> str | None:
    field = "primary_ip4" if record_type == "A" else "primary_ip6"
    ip = vm.get(field)
    address = ip.get("address") if isinstance(ip, dict) else None
    return str(ipaddress.ip_interface(address).ip) if address else None


def canonical_parts(name: str, zone: str) -> tuple[str, str]:
    name = name.rstrip(".").lower()
    zone = zone.rstrip(".").lower()
    suffix = f".{zone}"
    if name == zone:
        return "@", zone
    if not name.endswith(suffix):
        fail("dns.canonical_name must belong to dns.zone")
    return name[: -len(suffix)], zone


def relative_record_name(name: str, zone: str) -> str:
    """Return a NetBox DNS relative owner name for a relative/FQDN input."""
    name = name.rstrip(".").lower()
    zone = zone.rstrip(".").lower()
    suffix = f".{zone}"
    if name == zone:
        return "@"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def validate_static_record(record: dict, zone: str) -> tuple[str, str, str, int | None]:
    record_type = str(record.get("type", "")).upper()
    if record_type not in {"SRV", "TXT"}:
        fail("dns.records currently supports SRV and TXT records only")
    name = relative_record_name(str(record.get("name", "")), zone)
    value = str(record.get("value", "")).strip()
    if not name or name == "@":
        fail("dns.records entries require a non-root name")
    parts = value.split()
    if record_type == "TXT":
        if not value:
            fail("dns.records TXT values must not be empty")
        return name, record_type, value, None
    if len(parts) != 4 or not all(part.isdigit() for part in parts[:3]) or not parts[3]:
        fail("dns.records SRV values must be '<priority> <weight> <port> <target>'")
    # NetBox DNS canonicalizes SRV targets as FQDNs. Do not pass an already
    # absolute target with a trailing dot, otherwise the plugin appends its
    # own dot and produces values such as host.example..
    parts[3] = parts[3].rstrip(".")
    if not parts[3]:
        fail("dns.records SRV targets must contain a hostname")
    # NetBox DNS validates SRV values in the conventional four-field form.
    # Keep that API representation here; the PowerDNS adapter may normalize
    # the priority when it builds the authoritative record content.
    return name, record_type, " ".join(parts), int(parts[0])


def managed_marker(service: str) -> str:
    return f"Codex service canonical DNS: {service}"


def ensure_tag(client: NetBox, name: str) -> dict:
    item = client.one("/api/extras/tags/", {"slug": slug(name)})
    if item:
        return item
    return client.request("POST", "/api/extras/tags/", {
        "name": name, "slug": slug(name), "color": "00a0e3",
        "description": "Service health eligibility for canonical DNS",
    })


def mark_eligible(client: NetBox, hostname: str, service: str) -> None:
    vm = client.one("/api/virtualization/virtual-machines/", {"name": hostname})
    if not vm:
        fail(f"NetBox VM {hostname!r} does not exist")
    eligible = ensure_tag(client, "dns-eligible")
    tags = [tag["id"] if isinstance(tag, dict) else tag for tag in vm.get("tags", [])]
    if eligible["id"] not in tags:
        tags.append(eligible["id"])
        client.request("PATCH", f"/api/virtualization/virtual-machines/{vm['id']}/", {"tags": tags})
    print(f"Marked {hostname} DNS-eligible for {service}.")


def reconcile(client: NetBox, config: dict, ingress_targets: list[str]) -> None:
    service = config["service"]["name"]
    dns = config["dns"]
    routing = dns["routing"]
    owner, zone_name = canonical_parts(dns["canonical_name"], dns["zone"])
    zone = client.one("/api/plugins/netbox-dns/zones/", {"name": zone_name})
    if not zone:
        fail(f"NetBox DNS zone {zone_name!r} does not exist")
    marker = managed_marker(service)
    existing = [record for record in client.list("/api/plugins/netbox-dns/records/", {
        "zone_id": zone["id"],
    }) if record.get("description") == marker]

    desired: set[tuple[str, str, str]] = set()
    if routing["mode"] == "direct":
        record_type = routing.get("direct", {}).get("record_type", "A")
        allowed = {"A", "AAAA", "dual-stack"}
        if record_type not in allowed:
            fail("dns.routing.direct.record_type must be A, AAAA, or dual-stack")
        for vm in client.list("/api/virtualization/virtual-machines/", {"tag": slug(service)}):
            if "dns-eligible" not in tag_names(vm):
                continue
            for type_name in (["A", "AAAA"] if record_type == "dual-stack" else [record_type]):
                address = primary_address(vm, type_name)
                if address:
                    desired.add((owner, type_name, address, None))
        if not desired:
            fail(f"No DNS-eligible replicas found for direct service {service!r}")
    elif routing["mode"] == "ingress":
        if routing.get("ingress_service") != "traefik":
            fail("ingress DNS requires dns.routing.ingress_service: traefik")
        if not ingress_targets:
            fail("Traefik DNS targets are required for ingress reconciliation")
        for target in ingress_targets:
            try:
                address = ipaddress.ip_address(target)
                desired.add((owner, "A" if address.version == 4 else "AAAA", str(address), None))
            except ValueError:
                if len(ingress_targets) != 1:
                    fail("A CNAME ingress target must be the only Traefik DNS target")
                desired.add((owner, "CNAME", target.rstrip(".") + ".", None))
    elif routing["mode"] == "static":
        static = routing.get("static", {})
        record_type = static.get("record_type")
        address = static.get("address")
        if record_type not in {"A", "AAAA"}:
            fail("dns.routing.static.record_type must be A or AAAA")
        try:
            parsed_address = ipaddress.ip_address(str(address))
        except ValueError:
            fail("dns.routing.static.address must be a valid IP address")
        if (record_type == "A" and parsed_address.version != 4) or (
            record_type == "AAAA" and parsed_address.version != 6
        ):
            fail("dns.routing.static.address must match record_type")
        desired.add((owner, record_type, str(parsed_address), None))
    else:
        fail("dns.routing.mode must be direct, ingress, or static")

    for static_record in dns.get("records", []):
        desired.add(validate_static_record(static_record, zone_name))

    def record_key(record_name: str, record_type: str, value: str) -> tuple[str, str, str]:
        normalized_value = value.rstrip(".")
        if record_type == "SRV":
            parts = normalized_value.split()
            if len(parts) == 4 and parts[0].isdigit():
                # Existing records may have been manually normalized to the
                # PowerDNS content form without priority. Treat both forms
                # as the same desired record for idempotent reconciliation.
                normalized_value = " ".join(parts[1:])
        return record_name, record_type, normalized_value

    desired_values = {
        record_key(record_name, record_type, value): (value, priority)
        for record_name, record_type, value, priority in desired
    }
    desired_keys = set(desired_values)
    existing_keys = {
        record_key(str(record.get("name", "@")), record["type"], str(record["value"]))
        for record in existing
    }
    for record in existing:
        key = record_key(str(record.get("name", "@")), record["type"], str(record["value"]))
        if key not in desired_keys:
            client.request("DELETE", f"/api/plugins/netbox-dns/records/{record['id']}/")
            continue
        desired_record = desired_values.get(key)
        if desired_record is not None and record["type"] != "SRV" and str(record["value"]) != desired_record[0]:
            patch = {
                "value": desired_record[0],
                "status": "active",
                "description": marker,
                "ttl": int(dns.get("ttl", 60)),
            }
            client.request("PATCH", f"/api/plugins/netbox-dns/records/{record['id']}/", patch)
    for record_name, record_type, value, priority in sorted(desired):
        normalized = record_key(record_name, record_type, value)
        if normalized in existing_keys:
            continue
        payload = {
            "zone": zone["id"], "name": record_name, "type": record_type, "value": value,
            "status": "active", "description": marker, "ttl": int(dns.get("ttl", 60)),
        }
        if record_type == "SRV":
            payload["priority"] = priority or 0
        if record_type in {"A", "AAAA"}:
            payload["disable_ptr"] = True
        client.request("POST", "/api/plugins/netbox-dns/records/", payload)
    print(f"Reconciled {dns['canonical_name']}: {', '.join(f'{name} {kind} {value}' for name, kind, value, _ in sorted(desired))}")


def reconcile_record(
    client: NetBox, name: str, zone_name: str, record_type: str, value: str, ttl: int, owner: str
) -> None:
    zone = client.one("/api/plugins/netbox-dns/zones/", {"name": zone_name})
    if not zone:
        fail(f"NetBox DNS zone {zone_name!r} does not exist")
    relative_name = relative_record_name(name, zone_name)
    marker = f"Codex external DNS: {owner}"
    records = client.list("/api/plugins/netbox-dns/records/", {"zone_id": zone["id"]})
    record_type = record_type.upper()
    if record_type not in {"A", "CNAME"}:
        fail("reconcile-record supports only A and CNAME records")
    desired_key = (relative_name, record_type, value.rstrip("."))
    existing_keys = set()
    for record in records:
        if record.get("description") != marker:
            continue
        current_key = (
            str(record.get("name", "")),
            str(record.get("type", "")).upper(),
            str(record.get("value", "")).rstrip("."),
        )
        existing_keys.add(current_key)
        if current_key != desired_key:
            client.request("DELETE", f"/api/plugins/netbox-dns/records/{record['id']}/")
    if desired_key not in existing_keys:
        client.request("POST", "/api/plugins/netbox-dns/records/", {
            "zone": zone["id"],
            "name": relative_name,
            "type": record_type,
            "value": value,
            "status": "active",
            "description": marker,
            "ttl": ttl,
            **({"disable_ptr": True} if record_type == "A" else {}),
        })
    print(f"Reconciled {name} {record_type} {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["mark-eligible", "reconcile", "reconcile-record"])
    parser.add_argument("--config")
    parser.add_argument("--hostname")
    parser.add_argument("--ingress-target", action="append", default=[])
    parser.add_argument("--name")
    parser.add_argument("--zone")
    parser.add_argument("--type")
    parser.add_argument("--value")
    parser.add_argument("--ttl", type=int, default=60)
    parser.add_argument("--owner")
    args = parser.parse_args()
    config = (
        yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
        if args.config else None
    )
    client = NetBox(os.environ.get("NETBOX_API", ""), os.environ.get("NETBOX_TOKEN", ""),
                    os.environ.get("NETBOX_VALIDATE_CERTS", "true").lower() == "true")
    if not client.url or not os.environ.get("NETBOX_TOKEN"):
        fail("NETBOX_API and NETBOX_TOKEN must be set")
    if args.action == "reconcile-record":
        if not all((args.name, args.zone, args.type, args.value, args.owner)):
            fail("reconcile-record requires --name, --zone, --type, --value and --owner")
        reconcile_record(client, args.name, args.zone, args.type, args.value, args.ttl, args.owner)
    elif args.action == "mark-eligible":
        if not args.hostname:
            fail("--hostname is required for mark-eligible")
        if not config:
            fail("--config is required for mark-eligible")
        mark_eligible(client, args.hostname, config["service"]["name"])
    else:
        if not config:
            fail("--config is required for reconcile")
        targets = args.ingress_target
        if not targets:
            targets = [target.strip() for target in os.environ.get("TRAEFIK_DNS_TARGETS", "").split(",") if target.strip()]
        reconcile(client, config, targets)


if __name__ == "__main__":
    main()
