#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import ipaddress
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def read_query():
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


def host(address):
    return str(ipaddress.ip_interface(address).ip)


class RestNetBox:
    def __init__(self, url, token, validate_certs):
        self.url = url.rstrip("/")
        self.context = None if validate_certs else ssl._create_unverified_context()
        self.headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        }

    def get_json(self, path, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=90) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            fail(f"NetBox GET {path} failed with HTTP {exc.code}: {body}")
        except urllib.error.URLError as exc:
            fail(f"NetBox GET {path} failed: {exc}")

    def list(self, path, params=None):
        params = dict(params or {})
        params.setdefault("limit", 1000)
        payload = self.get_json(path, params)
        if isinstance(payload, list):
            return payload
        results = list(payload.get("results", []))
        next_url = payload.get("next")
        while next_url:
            parsed = urllib.parse.urlparse(next_url)
            payload = self.get_json(parsed.path, urllib.parse.parse_qs(parsed.query))
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
        return results


def one_named(client, path, params, label):
    items = client.list(path, params)
    if len(items) != 1:
        fail(f"Expected exactly one NetBox {label}, found {len(items)}")
    return items[0]


def main():
    query = read_query()
    missing = [key for key in ["netbox_url", "netbox_token", "hostname"] if not clean(query.get(key))]
    if missing:
        fail(f"Missing external data query keys: {', '.join(missing)}")

    client = RestNetBox(
        query["netbox_url"],
        query["netbox_token"],
        bool_value(query.get("netbox_validate_certs", "true")),
    )

    vm_params = {"name": query["hostname"]}
    cluster_name = clean(query.get("netbox_cluster_name"))
    if cluster_name:
        cluster = one_named(client, "/api/virtualization/clusters/", {"name": cluster_name}, f"cluster {cluster_name!r}")
        vm_params["cluster_id"] = cluster["id"]

    vm = one_named(client, "/api/virtualization/virtual-machines/", vm_params, f"VM {query['hostname']!r}")
    primary_ip4 = vm.get("primary_ip4")
    if not primary_ip4 or not clean(primary_ip4.get("address")):
        fail(f"NetBox VM {query['hostname']!r} has no primary_ip4 address")

    print(json.dumps({"ip": host(primary_ip4["address"])}))


if __name__ == "__main__":
    main()
