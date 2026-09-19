#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the declarative OpenStack platform pipeline phases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLATFORM_FILE = ROOT / "environments/example/platform.yaml"
HOST_INVENTORY = ROOT / "platform/openstack/kolla/inventory/example"
HOST_PLAYBOOK = ROOT / "platform/openstack/host/ansible/host-bootstrap.yml"
FOUNDATION_ROOTS = {
    "identity": "identity",
    "network": "network",
    "security": "security",
    "compute-catalog": "compute-catalog",
    "images": "images",
}
FOUNDATION_GENERATOR = ROOT / "platform/openstack/scripts/generate-foundation.py"

KOLLA_CORE_GROUPS = {
    "control", "network", "compute", "storage", "monitoring", "deployment",
    "baremetal", "common", "tls-backend", "etcd", "hacluster", "hacluster-remote",
    "loadbalancer", "mariadb", "rabbitmq", "memcached", "cron", "fluentd",
    "kolla_logs", "kolla_toolbox", "openvswitch", "iscsid", "tgtd", "multipathd",
    "openstack_hosts", "ovn-controller", "ovn-controller-compute", "ovn-controller-network",
    "ovn-database", "ovn-northd", "ovn-nb-db", "ovn-sb-db", "ovn-sb-db-relay",
}

KOLLA_SERVICE_DEPENDENCIES = {
    "keystone": {"keystone"},
    "glance": {"glance"},
    "nova": {"nova", "placement"},
    "placement": {"placement"},
    # Kolla's neutron service-precheck validates these integration groups even
    # when the corresponding optional services are disabled.
    "neutron": {"neutron", "openvswitch", "ironic-neutron-agent", "manila-share"},
    "cinder": {"cinder"},
    "horizon": {"horizon"},
    "skyline": {"skyline"},
}


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def add_no_proxy_hosts(env: dict[str, str], hosts: list[str]) -> None:
    """Keep private OpenStack endpoints out of the runner's outbound proxy."""
    configured = {
        value.strip()
        for variable in ("NO_PROXY", "no_proxy")
        for value in env.get(variable, "").split(",")
        if value.strip()
    }
    configured.update(host.strip() for host in hosts if host.strip())
    value = ",".join(sorted(configured))
    env["NO_PROXY"] = value
    env["no_proxy"] = value


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env or os.environ.copy(), check=False)
    if result.returncode:
        fail(f"command failed with exit code {result.returncode}: {command[0]}")


def config() -> dict[str, Any]:
    document = yaml.safe_load(PLATFORM_FILE.read_text(encoding="utf-8")) or {}
    openstack = document.get("openstack")
    if not isinstance(openstack, dict) or not openstack.get("enabled"):
        fail("openstack.enabled must be true")
    return openstack


def generate_foundation() -> None:
    """Render foundation Terraform roots for the current CI/workspace run."""
    run(["python3", str(FOUNDATION_GENERATOR)])


def foundation_root(component: str) -> Path:
    project = os.environ.get("OPENSTACK_FOUNDATION_PROJECT", "services").strip()
    if not project or Path(project).name != project or project in {".", ".."}:
        fail("OPENSTACK_FOUNDATION_PROJECT must be a simple project directory name")
    directory = FOUNDATION_ROOTS.get(component)
    if directory is None:
        fail(f"unknown OpenStack Foundation component: {component}")
    return ROOT / "platform/openstack/foundation/projects" / project / directory


def load_platform_secrets(openstack: dict[str, Any]) -> None:
    platform = yaml.safe_load(PLATFORM_FILE.read_text(encoding="utf-8")) or {}
    secret_config = openstack.get("secrets", {})
    openbao = platform.get("openbao", {})
    if not isinstance(secret_config, dict) or not secret_config.get("fields"):
        return
    if not isinstance(openbao, dict):
        fail("platform.openbao must be configured for platform secrets")
    mount, path = str(secret_config["path"]).split("/", 1)
    specs = [
        {
            "environment_name": f"OPENSTACK_SECRET_{str(field).upper()}",
            "mount": mount,
            "path": path,
            "field": field,
            "file": field == "kolla_passwords",
            "encoding": "base64" if field == "kolla_passwords" else "plain",
            "job_types": ["platform"],
        }
        for field in secret_config["fields"]
    ]
    common_names = {
        "ansible_private_key_file": "ANSIBLE_PRIVATE_KEY_FILE",
        "ansible_ci_password": "ANSIBLE_CI_PASSWORD",
        "netbox_token": "NETBOX_TOKEN",
        "root_password": "ROOT_PASSWORD",
        "terraform_state_password": "PGPASSWORD",
    }
    common_fields = openbao.get("common_secrets", {}).get("fields", [])
    specs.extend(
        {
            "environment_name": common_names[field],
            "mount": "kv",
            "path": "example-cluster",
            "field": field,
            "file": field == "ansible_private_key_file",
            "job_types": ["platform"],
        }
        for field in common_fields
        if field in common_names
    )
    env = os.environ.copy()
    env.update({
        "OPENBAO_SECRET_JOB": "platform",
        "OPENBAO_SECRET_SPECS": json.dumps(specs),
        "VAULT_SERVER_URL": str(openbao["server_url"]),
        "VAULT_AUTH_PATH": str(openbao.get("auth_path", "gitlab-ci")),
        "VAULT_AUTH_ROLE": str(openbao.get("auth_role", "ci")),
        "OPENBAO_ID_TOKEN_NAME": str(openbao.get("id_token_name", "OPENBAO_ID_TOKEN")),
        "OPENBAO_OIDC_AUDIENCE": str(openbao.get("forgejo_audience", openbao["server_url"])),
    })
    with tempfile.NamedTemporaryFile(prefix="openstack-secrets-", delete=False) as output:
        output_path = Path(output.name)
    try:
        run(["python3", str(ROOT / "shared/scripts/load_openbao_secrets.py"), str(output_path)], env=env)
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("export "):
                name, value = line[7:].split("=", 1)
                parsed = shlex.split(value)
                env[name] = parsed[0] if parsed else ""
        exported_names = {spec["environment_name"] for spec in specs}
        os.environ.update({key: env[key] for key in exported_names if key in env})
        netbox = platform.get("netbox", {})
        if isinstance(netbox, dict) and netbox.get("url"):
            os.environ["NETBOX_API"] = str(netbox["url"]).rstrip("/")
            os.environ["NETBOX_VALIDATE_CERTS"] = str(
                netbox.get("validate_certs", True)
            ).lower()
    finally:
        output_path.unlink(missing_ok=True)


def ensure_openstack_dns(openstack: dict[str, Any]) -> None:
    dns = openstack.get("dns", {})
    if not isinstance(dns, dict):
        fail("openstack.dns must be a mapping")
    zone = str(dns.get("zone", "")).strip()
    records = dns.get("records", [])
    if not zone or not isinstance(records, list) or not records:
        fail("openstack.dns requires a non-empty zone and records list")
    if not os.environ.get("NETBOX_API") or not os.environ.get("NETBOX_TOKEN"):
        fail("NETBOX_API and NETBOX_TOKEN are required to reconcile OpenStack DNS")
    for record in records:
        if not isinstance(record, dict):
            fail("each openstack.dns.records item must be a mapping")
        hostname = str(record.get("hostname", "")).strip()
        record_type = str(record.get("record_type", "CNAME")).upper().strip()
        target = str(record.get("target", "")).strip()
        if not hostname or record_type != "CNAME" or not target:
            fail("each OpenStack DNS record requires hostname, record_type=CNAME and target")
        run([
            "python3", str(ROOT / "shared/scripts/reconcile_service_dns.py"),
            "reconcile-record",
            "--name", hostname,
            "--zone", zone,
            "--type", record_type,
            "--value", target,
            "--ttl", str(int(record.get("ttl", dns.get("ttl", 60)))),
            "--owner", f"openstack/{hostname}",
        ])
def host(phase: str) -> None:
    env = os.environ.copy()
    # ANSIBLE_USER is a project-level variable used by the shared inventory.
    # The OpenStack inventory is standalone, so pass the connection variables
    # explicitly instead of allowing SSH to fall back to the runner account.
    ansible_user = env.get("ANSIBLE_USER") or "root"
    private_key = env.get("ANSIBLE_PRIVATE_KEY_FILE", "")
    command = [
        "ansible-playbook", "-i", str(HOST_INVENTORY), str(HOST_PLAYBOOK),
        "--limit", "openstack_hosts",
        "-e", f"ansible_user={ansible_user}",
    ]
    if private_key:
        command.extend(["-e", f"ansible_ssh_private_key_file={private_key}"])
    if phase == "host-preflight":
        command.append("--check")
    env["ANSIBLE_REMOTE_USER"] = ansible_user
    run(command, env=env)


def resolve_kolla_federation_files(globals_document: dict[str, Any]) -> None:
    """Resolve committed federation assets for Kolla's controller-side copy tasks.

    Kolla's Keystone federation role treats ``keystone_identity_mappings[*].file``
    as a source path on the Ansible controller.  The upstream example uses
    ``/etc/kolla/config/...``, which is not present in the CI checkout when a
    custom ``--configdir`` is used.  Keep that declarative value in globals.yml,
    but resolve its basename to the reviewed repository asset before invoking
    Kolla.
    """
    mappings = globals_document.get("keystone_identity_mappings", [])
    if not isinstance(mappings, list):
        fail("keystone_identity_mappings must be a list")

    federation_root = ROOT / "platform/openstack/kolla/federation"
    for mapping in mappings:
        if not isinstance(mapping, dict) or not mapping.get("file"):
            fail("each keystone identity mapping must define a file")
        source = Path(str(mapping["file"]))
        if not source.is_file():
            source = federation_root / source.name
        if not source.is_file():
            fail(f"Keystone federation mapping file is missing: {mapping['file']}")
        mapping["file"] = str(source.resolve())

    metadata_root = ROOT / "platform/openstack/kolla/federation/metadata"
    providers = globals_document.get("keystone_identity_providers", [])
    if not isinstance(providers, list):
        fail("keystone_identity_providers must be a list")
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("protocol") != "openid":
            continue
        identifier = str(provider.get("identifier", "")).rstrip("/")
        parsed_identifier = urlsplit(identifier)
        metadata_name = quote(
            f"{parsed_identifier.netloc}{parsed_identifier.path}", safe=""
        )
        source = Path(str(provider.get("metadata_folder", "")))
        if not source.is_dir():
            source = metadata_root
        if not source.is_dir():
            fail(f"Keystone OIDC metadata directory is missing: {provider.get('metadata_folder')}")
        for suffix in (".provider", ".client", ".conf"):
            metadata_file = source / f"{metadata_name}{suffix}"
            if not metadata_file.is_file():
                fail(f"Keystone OIDC metadata file is missing: {metadata_file}")
        provider["metadata_folder"] = str(source.resolve())


def _inventory_sections(path: Path) -> list[tuple[str, str, list[str]]]:
    sections: list[tuple[str, str, list[str]]] = []
    current_name: str | None = None
    current_header = ""
    current_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        match = re.match(r"^\[([^]]+)\]\s*$", line.rstrip("\n"))
        if match:
            if current_name is not None:
                sections.append((current_name, current_header, current_lines))
            current_name = match.group(1)
            current_header = line
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections.append((current_name, current_header, current_lines))
    return sections


def generate_kolla_inventory(source: Path, destination: Path, services: dict[str, Any]) -> None:
    """Write a minimal inventory without modifying the tracked inventory."""
    if not isinstance(services, dict):
        fail("openstack.kolla.services must be a mapping")
    enabled = {str(name) for name, value in services.items() if value}
    roots = set(KOLLA_CORE_GROUPS)
    for service in enabled:
        roots.update(KOLLA_SERVICE_DEPENDENCIES.get(service, {service}))

    sections = _inventory_sections(source)
    by_name = {name: (header, lines) for name, header, lines in sections}
    selected = set(roots)
    # Kolla's service-precheck validates every service group belonging to an
    # enabled parent (including disabled optional children such as
    # nova-spicehtml5proxy). Keep the complete service family, not only the
    # groups reachable through ``:children`` relationships.
    service_families = set().union(
        *(KOLLA_SERVICE_DEPENDENCIES.get(service, {service}) for service in enabled)
    )
    for name in by_name:
        base_name = name.removesuffix(":children")
        if any(base_name == service or base_name.startswith(f"{service}-") for service in service_families):
            selected.add(base_name)
    changed = True
    while changed:
        changed = False
        for name in tuple(selected):
            section = by_name.get(f"{name}:children")
            if not section:
                continue
            for line in section[1]:
                child = line.strip()
                if child and not child.startswith((';', '#')) and child in by_name and child not in selected:
                    selected.add(child)
                    changed = True

    missing = sorted(
        root for root in roots
        if root not in by_name and f"{root}:children" not in by_name
    )
    if missing:
        fail(f"Kolla inventory is missing required groups: {', '.join(missing)}")

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output: list[str] = []
    for name, header, lines in sections:
        base_name = name.removesuffix(":children")
        if base_name in selected:
            output.extend([header, *lines])
    destination.write_text("".join(output), encoding="utf-8")


def filter_kolla_volumes(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: [item for item in filter_kolla_volumes(items) if item not in (None, "")]
            if key == "volumes" and isinstance(items, list)
            else filter_kolla_volumes(items)
            for key, items in value.items()
        }
    if isinstance(value, list):
        return [filter_kolla_volumes(item) for item in value]
    return value


def ensure_memcache_secret_key(
    virtualenv: Path,
    inventory: Path,
    passwords_file: Path,
    env: dict[str, str],
) -> None:
    """Flush only memcached when Kolla's token-cache key changes.

    The digest is persisted on the OpenStack host, rather than in the CI
    workspace, so different runners share the same deploy state. The raw
    secret never enters the command line, logs, or marker file.
    """
    passwords = yaml.safe_load(passwords_file.read_text(encoding="utf-8")) or {}
    secret = passwords.get("memcache_secret_key")
    if not isinstance(secret, str) or not secret:
        fail("Kolla passwords must define a non-empty memcache_secret_key")
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    remote_script = (
        "set -eu; "
        "marker=/etc/kolla/.drg-memcache-secret.sha256; "
        f"expected={digest}; "
        "previous=$(cat \"$marker\" 2>/dev/null || true); "
        "if [ \"$previous\" != \"$expected\" ]; then "
        "docker exec memcached perl -MIO::Socket::INET -e "
        "'my $s=IO::Socket::INET->new(PeerAddr=>\"192.0.2.2\",PeerPort=>11211,"
        "Proto=>\"tcp\",Timeout=>5) or die \"memcached unavailable\\n\"; "
        "print $s \"flush_all\\r\\n\"; my $r=<$s>; die \"flush failed\\n\" "
        "unless defined($r) && $r =~ /^OK/;' "
        "printf \"%s\\n\" \"$expected\" > \"$marker\"; "
        "chmod 600 \"$marker\"; echo memcache_cache=flushed; "
        "else echo memcache_cache=unchanged; fi"
    )
    run([
        str(virtualenv / "bin" / "ansible"),
        "openstack_hosts",
        "-i", str(inventory),
        "-m", "ansible.builtin.shell",
        "-a", remote_script,
    ], env=env)


def kolla(phase: str, openstack: dict[str, Any]) -> None:
    kolla_config = openstack.get("kolla", {})
    virtualenv = ROOT / str(kolla_config.get("virtualenv", ".ci-kolla-ansible"))
    kolla_bin = virtualenv / "bin" / "kolla-ansible"
    if not kolla_bin.exists():
        run(["python3", "-m", "venv", str(virtualenv)])
        run([
            str(virtualenv / "bin" / "pip"), "install", "--upgrade",
            f"kolla-ansible=={kolla_config.get('kolla_ansible_version', '22.1.0')}",
        ])
    kolla_share = virtualenv / "share" / "kolla-ansible"
    collections_path = ROOT / ".ansible" / "collections"
    collections_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    collection_marker = ROOT / ".ansible" / ".kolla-requirements.sha256"
    collection_requirements = (
        kolla_config.get("kolla_ansible_version", "22.1.0"),
        kolla_share / "requirements-core.yml",
        kolla_share / "requirements.yml",
    )
    requirements_digest = hashlib.sha256()
    for requirement in collection_requirements:
        if isinstance(requirement, Path):
            requirements_digest.update(str(requirement.name).encode("utf-8"))
            requirements_digest.update(requirement.read_bytes())
        else:
            requirements_digest.update(str(requirement).encode("utf-8"))
    digest = requirements_digest.hexdigest()
    # The marker and the collection directory are cached independently by
    # some CI backends. Do not trust a matching marker if the module Kolla
    # needs is absent; otherwise a stale/incomplete cache reaches deploy and
    # fails later with "couldn't resolve module/action".
    kolla_module_probe = (
        collections_path / "ansible_collections" / "community" / "general"
        / "plugins" / "modules" / "modprobe.py"
    )
    collections_ready = kolla_module_probe.is_file()
    requirements_current = (
        collection_marker.is_file()
        and collection_marker.read_text(encoding="utf-8").strip() == digest
    )
    if not (requirements_current and collections_ready):
        run([
            str(virtualenv / "bin" / "ansible-galaxy"), "collection", "install",
            "--force",
            "-r", str(kolla_share / "requirements-core.yml"),
            "-p", str(collections_path),
        ])
        run([
            str(virtualenv / "bin" / "ansible-galaxy"), "collection", "install",
            "--force",
            "-r", str(kolla_share / "requirements.yml"),
            "-p", str(collections_path),
        ])
        collection_marker.write_text(f"{digest}\n", encoding="utf-8")
    command_name = {
        "kolla-prechecks": "prechecks",
        "kolla-deploy": "deploy",
    }[phase]
    inventory_source = ROOT / str(kolla_config.get("inventory", "platform/openstack/kolla/inventory/example"))
    globals_file = str(ROOT / str(kolla_config.get("globals_file", "platform/openstack/kolla/globals.yml.example")))
    config_dir = ROOT / ".ci-kolla-config"
    config_dir.mkdir(mode=0o700, exist_ok=True)
    config_dir.chmod(0o700)
    (config_dir / "config" / "neutron" / "plugins").mkdir(mode=0o700, parents=True, exist_ok=True)
    skyline_config = ROOT / "platform/openstack/kolla/skyline/skyline.yaml"
    if kolla_config.get("services", {}).get("skyline") and skyline_config.is_file():
        skyline_destination = config_dir / "config" / "skyline" / "skyline.yaml"
        skyline_destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(skyline_config, skyline_destination)
        skyline_destination.chmod(0o600)
    controller_config = config_dir / "ansible.cfg"
    globals_document = yaml.safe_load(Path(globals_file).read_text(encoding="utf-8")) or {}
    globals_document.update({
        "openstack_release": str(openstack.get("release", globals_document.get("openstack_release", "2026.1"))),
        "kolla_internal_vip_address": str(kolla_config.get("internal_vip", globals_document.get("kolla_internal_vip_address", ""))),
        "network_interface": str(kolla_config.get("management_interface", globals_document.get("network_interface", ""))),
        "neutron_external_interface": str(kolla_config.get("external_interface", globals_document.get("neutron_external_interface", ""))),
        "neutron_plugin_agent": str(kolla_config.get("neutron_plugin_agent", globals_document.get("neutron_plugin_agent", "ovn"))),
        "nova_compute_virt_type": str(kolla_config.get("nova_compute_virt_type", globals_document.get("nova_compute_virt_type", "kvm"))),
    })
    resolve_kolla_federation_files(globals_document)
    # Kolla derives this value with ansible.utils.ipaddr(). Compute the
    # address equality here as well, so prechecks remain deterministic even
    # when a runner's Ansible collection loader does not expose that filter
    # while evaluating group_vars.
    internal_vip = str(globals_document.get("kolla_internal_vip_address", ""))
    external_vip = str(
        kolla_config.get(
            "external_vip",
            globals_document.get("kolla_external_vip_address", internal_vip),
        )
    )
    globals_document["kolla_same_external_internal_vip"] = internal_vip.split("/", 1)[0] == external_vip.split("/", 1)[0]
    for service, enabled in kolla_config.get("services", {}).items():
        globals_document[f"enable_{service.replace('-', '_')}"] = "yes" if enabled else "no"
    globals_document["enable_cinder_backend_lvm"] = "yes" if kolla_config.get("cinder_backend") == "lvm" else "no"
    globals_document = filter_kolla_volumes(globals_document)
    (config_dir / "globals.yml").write_text(yaml.safe_dump(globals_document, sort_keys=False), encoding="utf-8")
    inventory_path = config_dir / "inventory"
    generate_kolla_inventory(inventory_source, inventory_path, kolla_config.get("services", {}))
    passwords_value = os.environ.get("OPENSTACK_SECRET_KOLLA_PASSWORDS", "")
    if not passwords_value or not Path(passwords_value).is_file():
        fail("OPENSTACK_SECRET_KOLLA_PASSWORDS must be a file secret from OpenBao")
    # Kolla 22.x validates --passwords, but builds its Ansible command with
    # <configdir>/passwords.yml. Keep the OpenBao temporary file as the source
    # and materialize the expected controller-side path with strict permissions.
    passwords_file = config_dir / "passwords.yml"
    shutil.copyfile(passwords_value, passwords_file)
    passwords_file.chmod(0o600)
    command = [
        str(kolla_bin), command_name,
        "-i", str(inventory_path),
        "--configdir", str(config_dir),
    ]
    if phase == "kolla-prechecks" and kolla_config.get("use_test_images", False):
        command.append("--use-test-images")
    env = os.environ.copy()
    # Kolla invokes ansible-playbook as a child process. Ensure it resolves
    # the Ansible installed in the pinned Kolla virtualenv, not the runner's
    # system Ansible (which cannot import kolla_ansible filter plugins).
    env["PATH"] = f"{virtualenv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    # A runner-level ANSIBLE_LIMIT must not leak into Kolla's full-inventory
    # prechecks; Kolla derives its own host groups from this inventory.
    env.pop("ANSIBLE_LIMIT", None)
    collection_roots = os.pathsep.join(
        [
            str(collections_path),
            str(virtualenv / "share" / "ansible" / "collections"),
            "/usr/share/ansible/collections",
        ]
    )
    env["ANSIBLE_COLLECTIONS_PATH"] = collection_roots
    # Keep compatibility with older Ansible launchers that used the plural
    # spelling even though ansible-core 2.20 exposes the singular variable.
    env["ANSIBLE_COLLECTIONS_PATHS"] = collection_roots
    controller_config.write_text(
        "[defaults]\n"
        f"collections_path = {collection_roots}\n"
        f"roles_path = {kolla_share / 'ansible' / 'roles'}:{ROOT / 'shared' / 'ansible' / 'roles'}\n"
        "interpreter_python = auto_silent\n",
        encoding="utf-8",
    )
    controller_config.chmod(0o600)
    env["ANSIBLE_CONFIG"] = str(controller_config)
    # The memcached guard runs through Ansible before kolla-ansible starts.
    # Set the connection user before that hook; otherwise Ansible falls back
    # to the GitLab runner account instead of the configured SSH user.
    env["ANSIBLE_REMOTE_USER"] = env.get("ANSIBLE_USER") or "root"
    if phase == "kolla-deploy":
        ensure_memcache_secret_key(virtualenv, inventory_path, passwords_file, env)
    # Validate collection discovery with the exact environment that Kolla
    # passes to ansible-playbook. This makes cache/path regressions explicit
    # instead of failing later inside the loadbalancer role.
    debug_enabled = os.environ.get("KOLLA_DEBUG", "").lower() in {"1", "true", "yes"}
    if debug_enabled or os.environ.get("CI_DEBUG_TRACE", "").lower() == "true":
        run([
            str(virtualenv / "bin" / "ansible-galaxy"), "collection", "list",
            "--collections-path", str(collections_path),
        ], env=env)
        run([str(virtualenv / "bin" / "ansible-config"), "dump", "--only-changed"], env=env)
        run([
            str(virtualenv / "bin" / "ansible-doc"), "-t", "module",
            "community.general.modprobe",
        ], env=env)
    # Kolla's gather-facts playbook contains an intentionally optional dynamic
    # group (all_using_limit_True). A runner-wide fatal mismatch policy makes
    # the absent group abort an otherwise valid full-inventory run.
    env["ANSIBLE_HOST_PATTERN_MISMATCH"] = "warning"
    run(command, env=env)


def foundation(phase: str, openstack: dict[str, Any]) -> None:
    components = openstack.get("foundation", {}).get("components", [])
    if not isinstance(components, list):
        fail("openstack.foundation.components must be a list")
    foundation_config = openstack.get("foundation", {})
    auth_url = str(foundation_config.get("auth_url", "")).strip()
    region = str(foundation_config.get("region", "RegionOne")).strip()
    passwords_path = os.environ.get("OPENSTACK_SECRET_KOLLA_PASSWORDS", "")
    if not auth_url:
        fail("openstack.foundation.auth_url must be configured")
    if not region:
        fail("openstack.foundation.region must be configured")
    if not passwords_path or not Path(passwords_path).is_file():
        fail("OPENSTACK_SECRET_KOLLA_PASSWORDS must be a file secret from OpenBao")
    passwords = yaml.safe_load(Path(passwords_path).read_text(encoding="utf-8")) or {}
    admin_password = passwords.get("keystone_admin_password")
    if not isinstance(admin_password, str) or not admin_password:
        fail("Kolla passwords file does not contain keystone_admin_password")

    # Foundation Terraform roots use the OpenStack provider directly. Reuse
    # the admin credential generated for the Kolla deployment, but keep it in
    # the process environment only; never write it to Terraform files/state.
    env = os.environ.copy()
    provider_cache = ROOT / ".terraform.d" / "plugin-cache"
    provider_cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(provider_cache)
    env.update({
        "TF_VAR_openstack_auth_url": auth_url,
        "TF_VAR_openstack_region": region,
        "OS_AUTH_URL": auth_url,
        "OS_USERNAME": "admin",
        "OS_PASSWORD": admin_password,
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_NAME": "admin",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_REGION_NAME": region,
        "OS_IDENTITY_API_VERSION": "3",
        "OS_INTERFACE": "internal",
    })
    # The service catalog points at the internal VIP. GitLab runners have a
    # default outbound proxy, which must not be used for this private address.
    add_no_proxy_hosts(
        env,
        [
            str(openstack.get("host", {}).get("internal_vip", "")),
            str(openstack.get("kolla", {}).get("internal_vip", "")),
            str(foundation_config.get("internal_vip", "")),
        ],
    )
    state_name = os.environ.get("TF_STATE_NAME", "openstack-foundation")
    if not env.get("PGPASSWORD"):
        fail("PGPASSWORD must be configured in OpenBao for the foundation state backend")
    env["PG_CONN_STR"] = os.environ.get(
        "PG_CONN_STR",
        "postgresql://terraform_state@postgresql.example.test:5432/terraform_state?sslmode=disable",
    )
    for component in components:
        root = foundation_root(component)
        component_env = env.copy()
        component_env.update({
            "PG_SCHEMA_NAME": re.sub(
                r"[^A-Za-z0-9_]", "_", f"terraform_{state_name}_{component}"
            ),
        })
        # Keep the admin authentication scope for foundation roots. Resources
        # that belong to a project receive its explicit project_id; scoping
        # the admin user to services makes Keystone reject authentication.
        run(["tofu", "init", "-reconfigure"], cwd=root, env=component_env)
        if phase == "foundation-plan":
            run(["tofu", "plan", "-out=tfplan"], cwd=root, env=component_env)
        else:
            plan = root / "tfplan"
            if not plan.exists():
                fail(f"Foundation plan is missing: {plan}")
            # The OpenStack provider credentials are short-lived and the plan
            # artifact may be applied on another runner. Recreate the plan
            # with the credentials loaded for this apply job before executing
            # it, otherwise Neutron can reject the embedded session with 401.
            run(["tofu", "plan", "-out=tfplan"], cwd=root, env=component_env)
            run(["tofu", "apply", "-auto-approve", "tfplan"], cwd=root, env=component_env)


def smoke(openstack: dict[str, Any]) -> None:
    kolla_config = openstack.get("kolla", {})
    client = shutil.which("openstack")
    if client is None:
        fail("OpenStack client is required for smoke checks; install python3-openstackclient on the GitLab Runner")

    passwords_path = os.environ.get("OPENSTACK_SECRET_KOLLA_PASSWORDS", "")
    if not passwords_path or not Path(passwords_path).is_file():
        fail("OPENSTACK_SECRET_KOLLA_PASSWORDS must be a file secret from OpenBao")
    passwords = yaml.safe_load(Path(passwords_path).read_text(encoding="utf-8")) or {}
    admin_password = passwords.get("keystone_admin_password")
    if not isinstance(admin_password, str) or not admin_password:
        fail("Kolla passwords file does not contain keystone_admin_password")

    foundation_config = openstack.get("foundation", {})
    auth_url = str(foundation_config.get("auth_url", "")).strip()
    region = str(foundation_config.get("region", "RegionOne")).strip()
    if not auth_url or not region:
        fail("OpenStack smoke authentication is not configured")
    env = os.environ.copy()
    env.update({
        "OS_AUTH_URL": auth_url,
        "OS_USERNAME": "admin",
        "OS_PASSWORD": admin_password,
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_NAME": "admin",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_REGION_NAME": region,
        "OS_IDENTITY_API_VERSION": "3",
        "OS_INTERFACE": "internal",
    })
    add_no_proxy_hosts(
        env,
        [
            str(openstack.get("host", {}).get("internal_vip", "")),
            str(kolla_config.get("internal_vip", "")),
            str(foundation_config.get("internal_vip", "")),
        ],
    )
    run([client, "token", "issue"], env=env)
    service_result = subprocess.run(
        [client, "service", "list", "-f", "json"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if service_result.returncode:
        fail(f"command failed with exit code {service_result.returncode}: {client}")
    try:
        service_rows = json.loads(service_result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"could not parse OpenStack service list: {exc}")
    available_services = {
        value.lower()
        for row in service_rows
        for value in (row.get("Type", ""), row.get("Name", ""))
        if isinstance(value, str) and value.strip()
    }
    service_aliases = {
        "keystone": {"keystone", "identity"},
        "glance": {"glance", "image"},
        "nova": {"nova", "compute"},
        "neutron": {"neutron", "network"},
        "cinder": {"cinder", "volume", "volumev2", "volumev3"},
        "placement": {"placement"},
    }
    required_services = {
        str(service)
        for service in openstack.get("smoke", {}).get("required_services", [])
    }
    missing_services = sorted(
        service for service in required_services
        if not (service_aliases.get(service, {service}) & available_services)
    )
    if missing_services:
        fail(f"OpenStack services are missing: {', '.join(missing_services)}")
    run([client, "compute", "service", "list"], env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=(
        "validate", "host-preflight", "host-apply", "kolla-prechecks",
        "kolla-deploy", "foundation-plan", "foundation-apply", "smoke",
    ))
    args = parser.parse_args()
    openstack = config()
    if args.phase == "validate" or args.phase.startswith("foundation-"):
        generate_foundation()
    load_platform_secrets(openstack)
    if args.phase == "validate":
        run(["ansible-playbook", "-i", str(HOST_INVENTORY), str(HOST_PLAYBOOK), "--syntax-check"])
        for component in openstack.get("foundation", {}).get("components", []):
            root = foundation_root(component)
            run(["tofu", "init", "-backend=false"], cwd=root)
            run(["tofu", "validate"], cwd=root)
    elif args.phase.startswith("host-"):
        host(args.phase)
    elif args.phase.startswith("kolla-"):
        kolla(args.phase, openstack)
        if args.phase == "kolla-deploy":
            ensure_openstack_dns(openstack)
    elif args.phase.startswith("foundation-"):
        ensure_openstack_dns(openstack)
        foundation(args.phase, openstack)
    else:
        smoke(openstack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
