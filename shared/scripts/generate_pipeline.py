#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from ansible_dependency_index import build_role_service_index

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:
    missing = exc.name or "required dependency"
    print(
        f"ERROR: Missing Python dependency {missing!r}. "
        "Install PyYAML and Jinja2, or run on the CI runner image that includes Ansible's Python stack.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


ZERO_SHA = "0000000000000000000000000000000000000000"
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "shared" / "templates" / "pipeline-services.yml"
TEMPLATE_DIR = REPO_ROOT / "shared" / "templates" / "pipeline"
GLOBAL_IMPACT_PREFIXES = (
    "environments/",
    # "services/freeipa/", just no
)
COMMON_OPENBAO_SECRET_JOBS = {
    "proxmox_api_token": ["terraform"],
    "root_password": ["terraform"],
    "ssh_public_keys": ["terraform"],
    "netbox_token": ["terraform", "ansible"],
    "ansible_private_key_file": ["terraform", "ansible"],
    "ansible_ci_password": ["ansible"],
    "ipa_enroller_password": ["terraform", "ansible"],
    "terraform_state_password": ["terraform", "ansible"],
}
COMMON_OPENBAO_SECRET_ENVIRONMENT_NAMES = {
    "ansible_private_key_file": {
        "terraform": "ANSIBLE_PRIVATE_KEY_FILE",
        "ansible": "ANSIBLE_PRIVATE_KEY_FILE",
    },
    "terraform_state_password": {"terraform": "PGPASSWORD"},
    "ansible_ci_password": {"ansible": "ANSIBLE_CI_PASSWORD"},
    "ipa_enroller_password": {"terraform": "IPA_ENROLLER_PASSWORD"},
}
OPENSTACK_FOUNDATION_ROOTS = {
    "identity": ("identity", "openstack-foundation-identity"),
    "network": ("network", "openstack-foundation-network"),
    "security": ("security", "openstack-foundation-security"),
    "compute-catalog": ("compute-catalog", "openstack-foundation-compute-catalog"),
    "images": ("images", "openstack-foundation-images"),
}


def openstack_foundation_project() -> str:
    project = os.environ.get("OPENSTACK_FOUNDATION_PROJECT", "services").strip()
    if not project or Path(project).name != project or project in {".", ".."}:
        raise SystemExit("ERROR: OPENSTACK_FOUNDATION_PROJECT must be a simple project directory name.")
    return project


def platform_openbao_job_config(platform: dict[str, Any]) -> dict[str, Any] | None:
    openstack = platform.get("openstack", {})
    if (
        not isinstance(openstack, dict)
        or not openstack.get("enabled")
    ):
        return None
    secret_config = openstack.get("secrets", {})
    if not isinstance(secret_config, dict):
        raise SystemExit("ERROR: openstack.secrets must be a mapping.")
    mount, secret_path = parse_openbao_path(
        secret_config.get("path"), "openstack.secrets.path"
    )
    fields = secret_config.get("fields", [])
    file_fields = secret_config.get("file_fields", {})
    if not isinstance(file_fields, dict) or not all(
        isinstance(field, str) and isinstance(encoding, str)
        for field, encoding in file_fields.items()
    ):
        raise SystemExit("ERROR: openstack.secrets.file_fields must map field names to encodings.")
    if not isinstance(fields, list) or not fields:
        raise SystemExit("ERROR: openstack.secrets.fields must be a non-empty list.")
    entries = []
    for field in fields:
        if not isinstance(field, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", field):
            raise SystemExit("ERROR: every openstack.secrets field must be lower_snake_case.")
        entries.append({
            "environment_name": f"OPENSTACK_SECRET_{upper_environment_name(field)}",
            "mount": mount,
            "path": secret_path,
            "field": field,
            "file": field in file_fields,
            "encoding": file_fields.get(field, "plain"),
            "job_types": ["platform"],
        })
    common_fields = platform.get("openbao", {}).get("common_secrets", {}).get("fields", [])
    common_file_fields = platform.get("openbao", {}).get("common_secrets", {}).get("file_fields", {})
    if not isinstance(common_file_fields, dict) or not all(
        isinstance(field, str) and isinstance(encoding, str)
        for field, encoding in common_file_fields.items()
    ):
        raise SystemExit("ERROR: openbao.common_secrets.file_fields must map field names to encodings.")
    common_names = {
        "ansible_private_key_file": "ANSIBLE_PRIVATE_KEY_FILE",
        "ansible_ci_password": "ANSIBLE_CI_PASSWORD",
        "netbox_token": "NETBOX_TOKEN",
        "root_password": "ROOT_PASSWORD",
        "terraform_state_password": "PGPASSWORD",
    }
    for field in common_fields:
        if field not in common_names:
            continue
        entries.append({
            "environment_name": common_names[field],
            "mount": "kv",
            "path": "example-cluster",
            "field": field,
            "file": field in common_file_fields,
            "encoding": common_file_fields.get(field, "plain"),
            "job_types": ["platform"],
        })
    return {
        "server_url": platform["openbao"]["server_url"],
        "auth_path": platform["openbao"]["auth_path"],
        "auth_role": platform["openbao"]["auth_role"],
        "id_token_name": platform["openbao"]["id_token_name"],
        "secrets": entries,
    }


OPENSTACK_PLATFORM_PHASES = (
    "validate",
    "host-preflight",
    "host-apply",
    "kolla-prechecks",
    "kolla-deploy",
    "foundation-plan",
    "foundation-apply",
    "smoke",
)


def _changed_path(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix.rstrip('/')}/")


def openstack_platform_phases(changed_files: list[str] | None) -> list[str]:
    """Return only platform phases affected by the current change set.

    ``None`` means that the provider did not expose a diff; in that case keep
    the safe historical behaviour and run the complete platform pipeline.
    An explicit empty list means that there are no platform changes.
    """
    if changed_files is None:
        return list(OPENSTACK_PLATFORM_PHASES)

    paths = {path.removeprefix("./") for path in changed_files}
    if not paths:
        return []

    if any(
        path in {
            "environments/example/platform.yaml",
            "shared/scripts/generate_pipeline.py",
            "shared/scripts/generate_woodpecker.py",
            "shared/scripts/pipeline_graph.py",
            "shared/scripts/run_platform.py",
            "shared/templates/gitlab-ci-template.yml",
            "shared/templates/pipeline-services.yml",
        }
        or _changed_path(path, "shared/templates/pipeline")
        for path in paths
    ):
        return list(OPENSTACK_PLATFORM_PHASES)

    phases: list[str] = ["validate"]
    if any(_changed_path(path, "platform/openstack/host") for path in paths):
        phases.extend(("host-preflight", "host-apply"))
    if any(_changed_path(path, "platform/openstack/kolla") for path in paths):
        phases.extend(("kolla-prechecks", "kolla-deploy"))
    if any(
        _changed_path(path, "platform/openstack/foundation")
        or _changed_path(path, "shared/terraform")
        for path in paths
    ):
        phases.extend(("foundation-plan", "foundation-apply"))

    if len(phases) == 1:
        return []
    phases.append("smoke")
    return phases


def openstack_platform_jobs(
    platform: dict[str, Any], changed_files: list[str] | None = None
) -> list[dict[str, Any]]:
    openstack = platform.get("openstack", {})
    if (
        not isinstance(openstack, dict)
        or not openstack.get("enabled")
    ):
        return []
    selected_phases = set(openstack_platform_phases(changed_files))
    if not selected_phases:
        return []
    openbao = platform_openbao_job_config(platform)
    phases = [
        ("validate", "platform-validate.yml.j2", None, False),
        ("host-preflight", "platform-phase.yml.j2", None, False),
        ("host-apply", "platform-phase.yml.j2", "platform:openstack:example:host-preflight", True),
        ("kolla-prechecks", "platform-phase.yml.j2", "platform:openstack:example:host-apply", False),
        ("kolla-deploy", "platform-phase.yml.j2", "platform:openstack:example:kolla-prechecks", True),
        ("foundation-plan", "platform-phase.yml.j2", "platform:openstack:example:kolla-deploy", False),
        ("foundation-apply", "platform-phase.yml.j2", "platform:openstack:example:foundation-plan", True),
        ("smoke", "platform-phase.yml.j2", "platform:openstack:example:foundation-apply", False),
    ]
    manual_apply_enabled = openstack.get("manual_apply", True)
    if not isinstance(manual_apply_enabled, bool):
        raise SystemExit("ERROR: openstack.manual_apply must be a boolean.")
    jobs = []
    foundation_project = openstack_foundation_project()
    foundation_plan_artifacts = [
        f"platform/openstack/foundation/projects/{foundation_project}/{OPENSTACK_FOUNDATION_ROOTS[component][0]}"
        for component in openstack.get("foundation", {}).get("components", [])
        if component in OPENSTACK_FOUNDATION_ROOTS
    ]
    previous_job_id = None
    for phase, template, _dependency, manual in phases:
        if phase not in selected_phases:
            continue
        jobs.append({
            "kind": "platform",
            "job_id": f"platform:openstack:example:{phase}",
            "environment_name": f"openstack/example/{phase}",
            "state_name": f"openstack-{phase}",
            "phase": phase,
            "template": template,
            "manual_apply": manual and manual_apply_enabled,
            "platform_needs": [previous_job_id] if previous_job_id else [],
            "foundation_plan_artifacts": foundation_plan_artifacts,
            "openbao": openbao,
        })
        previous_job_id = f"platform:openstack:example:{phase}"
    return jobs


def read_config() -> dict[str, Any]:
    if not CONFIG_FILE.is_file():
        raise SystemExit(f"ERROR: Config file {CONFIG_FILE.relative_to(REPO_ROOT)} does not exist.")

    with CONFIG_FILE.open("r", encoding="utf-8") as config_stream:
        config = yaml.safe_load(config_stream) or {}

    if not isinstance(config, dict):
        raise SystemExit("ERROR: Pipeline service config must be a YAML mapping.")

    config.setdefault("defaults", {})
    config.setdefault("services", {})
    if not isinstance(config["defaults"], dict) or not isinstance(config["services"], dict):
        raise SystemExit("ERROR: Pipeline service config 'defaults' and 'services' must be mappings.")

    return config


def changed_files_from_environment() -> list[str]:
    for variable in ("CHANGED_FILES_OVERRIDE", "FORGEJO_CHANGED_FILES"):
        override = os.environ.get(variable, "").strip()
        if not override:
            continue
        try:
            parsed_override = json.loads(override)
        except json.JSONDecodeError:
            parsed_override = None
        if isinstance(parsed_override, list) and all(
            isinstance(path, str) for path in parsed_override
        ):
            return [path for path in parsed_override if path]
        return override.split()

    woodpecker_files = os.environ.get("CI_PIPELINE_FILES", "")
    if woodpecker_files:
        try:
            parsed_files = json.loads(woodpecker_files)
        except json.JSONDecodeError:
            parsed_files = []
        if isinstance(parsed_files, list) and all(isinstance(path, str) for path in parsed_files):
            return [path for path in parsed_files if path]

    diff_base = "HEAD~1"
    merge_request_base = os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
    merge_request_target_sha = os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_SHA", "")
    forgejo_base = os.environ.get("FORGEJO_BASE_SHA", "")
    commit_before = os.environ.get("CI_COMMIT_BEFORE_SHA", "")
    pipeline_source = os.environ.get("CI_PIPELINE_SOURCE", "").strip().lower()
    merge_request_event_type = os.environ.get("CI_MERGE_REQUEST_EVENT_TYPE", "").strip().lower()
    is_gitlab = bool(os.environ.get("GITLAB_CI")) or pipeline_source in {
        "merge_request_event",
        "parent_pipeline",
        "web",
    }
    has_gitlab_merge_request = bool(
        os.environ.get("CI_MERGE_REQUEST_IID", "").strip()
        or pipeline_source == "merge_request_event"
    )
    if is_gitlab:
        if merge_request_base and merge_request_base != ZERO_SHA and has_gitlab_merge_request:
            diff_base = merge_request_base
        elif commit_before and commit_before != ZERO_SHA:
            diff_base = commit_before
    else:
        if forgejo_base and forgejo_base != ZERO_SHA:
            diff_base = forgejo_base
        elif commit_before and commit_before != ZERO_SHA:
            diff_base = commit_before
        elif merge_request_base and merge_request_base != ZERO_SHA:
            diff_base = merge_request_base

    merge_request_source_sha = os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA", "")
    if merge_request_source_sha == ZERO_SHA:
        merge_request_source_sha = ""
    commit_sha = os.environ.get("CI_COMMIT_SHA") or os.environ.get("FORGEJO_SHA", "HEAD")
    is_merged_result = merge_request_event_type in {"merged_result", "merge_train"}
    if (
        is_gitlab
        and has_gitlab_merge_request
        and is_merged_result
        and not merge_request_source_sha
        and merge_request_target_sha
        and merge_request_target_sha != ZERO_SHA
    ):
        # Older GitLab versions may omit CI_MERGE_REQUEST_SOURCE_BRANCH_SHA.
        # Comparing the merged result with the target branch tip still yields
        # only the merge request's changes.
        diff_base = merge_request_target_sha
    commit_sha = (
        merge_request_source_sha
        if is_gitlab and has_gitlab_merge_request and merge_request_source_sha
        else commit_sha
    )
    if (
        is_gitlab
        and has_gitlab_merge_request
        and is_merged_result
        and not merge_request_source_sha
        and (not merge_request_target_sha or merge_request_target_sha == ZERO_SHA)
        and commit_sha != "HEAD"
    ):
        # A merged-result commit is a two-parent merge commit. If GitLab did
        # not expose either MR SHA, its second parent is the source branch tip.
        commit_sha = f"{commit_sha}^2"
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", diff_base, commit_sha],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []

    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def has_global_pipeline_impact(
    changed_files: list[str],
    role_service_index: dict[str, set[str]] | None = None,
) -> bool:
    """Return whether a change can affect every service pipeline.

    The Woodpecker extension and the provider-neutral manifest must make the
    same service-selection decision.  Keep this rule in the shared generator
    for repository-wide CI, platform, and FreeIPA bootstrap changes.
    Service-specific dependencies under shared/ are handled by the per-service
    dependency index instead of being treated as globally impactful.
    """
    for raw_path in changed_files:
        path = raw_path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if path.startswith("shared/ansible/roles/") and role_service_index is not None:
            role_path = path.removeprefix("shared/ansible/roles/")
            role_name = role_path.split("/", 1)[0]
            if role_name in role_service_index:
                continue
        if any(path.startswith(prefix) for prefix in GLOBAL_IMPACT_PREFIXES):
            return True
    return False


@lru_cache(maxsize=None)
def shared_references_in_file(file_path: Path) -> frozenset[str]:
    """Return repository-relative shared paths mentioned by a source file."""
    try:
        contents = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return frozenset()

    references: set[str] = set()
    for quoted_value in re.findall(r"['\"]([^'\"]+)['\"]", contents):
        normalized = quoted_value.replace("\\", "/")
        marker = normalized.find("shared/")
        if marker < 0:
            continue
        reference = normalized[marker:].rstrip("/")
        references.add(reference)
    return frozenset(references)


def service_shared_dependencies(
    playbook: Path,
    terraform_root: Path,
) -> set[str]:
    """Build the shared paths consumed by one service's Ansible/Terraform."""
    dependencies = set(shared_references_in_file(playbook))
    if terraform_root.is_dir():
        for terraform_file in terraform_root.rglob("*.tf"):
            dependencies.update(shared_references_in_file(terraform_file))
    return dependencies


def path_matches_shared_dependency(path: str, dependency: str) -> bool:
    return path == dependency or path.startswith(f"{dependency.rstrip('/')}/")


def deployment_target_from_environment() -> tuple[str, str, str] | None:
    raw = os.environ.get("CI_PIPELINE_DEPLOY_TARGET", "").strip()
    if not raw:
        raw = os.environ.get("FORGEJO_DEPLOY_TARGET", "").strip()
    if not raw:
        if os.environ.get("CI_PIPELINE_EVENT", "").strip().lower() == "deployment":
            raise SystemExit(
                "ERROR: deployment requires CI_PIPELINE_DEPLOY_TARGET "
                "in the form service:cluster:contour."
            )
        return None
    parts = [part.strip() for part in raw.split(":")]
    if len(parts) != 3 or not all(parts):
        raise SystemExit(
            f"ERROR: invalid deployment target {raw!r}; expected service:cluster:contour."
        )
    return parts[0], parts[1], parts[2]


def slugify_environment(environment: str) -> str:
    slug = environment.lower()
    slug = re.sub(r"[^a-z0-9]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "production"


def deployment_targets(service_config: dict[str, Any], contours: list[str]) -> list[dict[str, str]]:
    placement = service_config.get("placement", {})
    clusters = placement.get("clusters", {}) if isinstance(placement, dict) else {}
    if not isinstance(clusters, dict):
        raise SystemExit("ERROR: service placement.clusters must be a mapping.")

    result: list[dict[str, str]] = []
    for cluster, cluster_config in clusters.items():
        if not isinstance(cluster, str) or not cluster or not isinstance(cluster_config, dict):
            raise SystemExit("ERROR: every placement cluster must be a non-empty mapping.")
        nodes = cluster_config.get("nodes", {})
        if not isinstance(nodes, dict):
            raise SystemExit(f"ERROR: placement.clusters.{cluster}.nodes must be a mapping.")
        active_contours: set[str] = set()
        for node in nodes.values():
            replica_counts = node.get("replicas_by_contour", {}) if isinstance(node, dict) else {}
            if not isinstance(replica_counts, dict):
                raise SystemExit(f"ERROR: placement.clusters.{cluster} replica counts must be a mapping.")
            for contour, replicas in replica_counts.items():
                if contour not in contours:
                    raise SystemExit(f"ERROR: deployment contour {contour!r} is not declared in platform config.")
                if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0:
                    raise SystemExit(f"ERROR: replica count for {cluster}/{contour} must be a non-negative integer.")
                if replicas > 0:
                    active_contours.add(contour)
        result.extend({"cluster": cluster, "contour": contour} for contour in contours if contour in active_contours)
    return result


def contour_rollout_enabled(service_config: dict[str, Any], description: str) -> bool:
    rollout = service_config.get("rollout", {})
    if rollout is None:
        return False
    if not isinstance(rollout, dict):
        raise SystemExit(f"ERROR: {description}.rollout must be a mapping.")
    enabled = rollout.get("by_contour", False)
    if not isinstance(enabled, bool):
        raise SystemExit(f"ERROR: {description}.rollout.by_contour must be a boolean.")
    return enabled


def read_platform(config: dict[str, Any]) -> dict[str, Any]:
    platform_file = REPO_ROOT / config["defaults"].get(
        "platform_config", "environments/example/platform.yaml"
    )
    try:
        with platform_file.open("r", encoding="utf-8") as platform_stream:
            platform = yaml.safe_load(platform_stream) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f"ERROR: Cannot read platform config {platform_file}: {exc}") from exc
    if not isinstance(platform, dict):
        raise SystemExit("ERROR: Platform config must be a YAML mapping.")
    return platform


def rollout_contours(platform: dict[str, Any]) -> list[str]:
    contour_map = platform.get("rollout_contours", {})
    if not isinstance(contour_map, dict) or not contour_map:
        raise SystemExit("ERROR: platform config must define rollout_contours.")
    try:
        return [
            name
            for name, _ in sorted(
                contour_map.items(), key=lambda item: item[1]["order"]
            )
        ]
    except (KeyError, TypeError) as exc:
        raise SystemExit("ERROR: every rollout contour requires a numeric order.") from exc


def discover_services(services_dir: Path) -> list[str]:
    if not services_dir.is_dir():
        return []
    return sorted(path.name for path in services_dir.iterdir() if path.is_dir())


def upper_environment_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def parse_openbao_path(path: Any, description: str) -> tuple[str, str]:
    if not isinstance(path, str) or path.count("/") < 1:
        raise SystemExit(f"ERROR: {description} must be '<mount>/<path>'.")
    mount, secret_path = path.split("/", 1)
    if not mount or not secret_path:
        raise SystemExit(f"ERROR: {description} must contain non-empty mount and path.")
    return mount, secret_path


def openbao_secret_entries(
    secret_config: Any,
    description: str,
    default_job_types: list[str],
    job_types_by_field: dict[str, list[str]] | None = None,
    environment_names_by_field: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if not secret_config:
        return []
    if not isinstance(secret_config, dict):
        raise SystemExit(f"ERROR: {description} must be a mapping.")
    mount, secret_path = parse_openbao_path(secret_config.get("path"), f"{description}.path")
    fields = secret_config.get("fields", [])
    if not isinstance(fields, list):
        raise SystemExit(f"ERROR: {description}.fields must be a list.")
    file_fields = secret_config.get("file_fields", {})
    if not isinstance(file_fields, dict) or not all(
        isinstance(field, str) and isinstance(encoding, str)
        for field, encoding in file_fields.items()
    ):
        raise SystemExit(f"ERROR: {description}.file_fields must map field names to encodings.")

    entries: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", field):
            raise SystemExit(
                f"ERROR: every {description}.fields item must be lower_snake_case."
            )
        job_types = (job_types_by_field or {}).get(field, default_job_types)
        environment_names = (environment_names_by_field or {}).get(field, {})
        file_value = field in file_fields
        destinations: list[tuple[str, list[str]]] = []
        if "terraform" in job_types:
            destinations.append((environment_names.get("terraform", f"TF_VAR_{field}"), ["terraform"]))
        if "ansible" in job_types:
            destinations.append((environment_names.get("ansible", upper_environment_name(field)), ["ansible"]))
        if (
            not destinations
            or not isinstance(file_value, bool)
        ):
            raise SystemExit(f"ERROR: {description}.fields.{field} has invalid file value.")
        merged_destinations: dict[str, list[str]] = {}
        for environment_name, destination_job_types in destinations:
            merged_destinations.setdefault(environment_name, []).extend(destination_job_types)
        entries.extend(
            {
                "environment_name": environment_name,
                "mount": mount,
                "path": secret_path,
                "field": field,
                "file": file_value,
                "encoding": file_fields.get(field, "plain"),
                "job_types": sorted(set(destination_job_types)),
            }
            for environment_name, destination_job_types in merged_destinations.items()
        )
    return entries


def openbao_job_config(
    platform: dict[str, Any], service_config: dict[str, Any], deployment_cluster: str | None
) -> dict[str, Any] | None:
    config = platform.get("openbao")
    if not config:
        return None
    if not isinstance(config, dict):
        raise SystemExit("ERROR: platform.openbao must be a mapping.")
    required = ("server_url", "auth_path", "auth_role", "id_token_name")
    if not all(isinstance(config.get(key), str) and config[key] for key in required):
        raise SystemExit("ERROR: platform.openbao requires server_url, auth_path, auth_role, and id_token_name.")
    common_secrets = config.get("common_secrets", {})
    if not isinstance(common_secrets, dict):
        raise SystemExit("ERROR: platform.openbao.common_secrets must be a mapping.")
    clusters = platform.get("clusters", {})
    if not isinstance(clusters, dict) or not clusters:
        raise SystemExit("ERROR: platform.clusters must be a non-empty mapping.")
    secret_cluster = deployment_cluster
    if secret_cluster is None:
        if len(clusters) != 1:
            raise SystemExit("ERROR: a deployment cluster is required to select common OpenBao secrets.")
        secret_cluster = next(iter(clusters))
    if secret_cluster not in clusters:
        raise SystemExit(f"ERROR: deployment cluster {secret_cluster!r} is not defined in platform.clusters.")
    entries = openbao_secret_entries(
        {
            "path": f"kv/{secret_cluster}",
            "fields": common_secrets.get("fields", []),
            "file_fields": common_secrets.get("file_fields", {}),
        },
        "platform.openbao.common_secrets",
        [],
        COMMON_OPENBAO_SECRET_JOBS,
        COMMON_OPENBAO_SECRET_ENVIRONMENT_NAMES,
    )
    entries.extend(openbao_secret_entries(service_config.get("secrets"), "service secrets", ["ansible"]))
    secret_imports = service_config.get("secret_imports")
    if isinstance(secret_imports, dict):
        secret_imports = [secret_imports]
    elif secret_imports is None:
        secret_imports = []
    elif not isinstance(secret_imports, list):
        raise SystemExit("ERROR: service secret imports must be a mapping or list of mappings.")
    for index, secret_import in enumerate(secret_imports):
        entries.extend(
            openbao_secret_entries(
                secret_import,
                f"service secret imports[{index}]",
                ["ansible"],
            )
        )
    names = [entry["environment_name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("ERROR: OpenBao secret environment variable names must be unique per service.")
    return {**{key: config[key] for key in required}, "secrets": entries}


def ansible_changed_for_service(
    service: str,
    playbook: str,
    changed_files: list[str],
    ansible_dir: str,
    terraform_dir: str,
    ansible_limit: str,
    role_service_index: dict[str, set[str]],
    shared_dependencies: set[str] | None = None,
) -> bool:
    service_ansible_prefix = f"services/{service}/{ansible_dir}/"
    service_root_prefix = f"services/{service}/"
    if any(
        path == playbook or path.startswith(service_ansible_prefix)
        for path in changed_files
    ):
        return True

    for path in changed_files:
        if not path.startswith(service_root_prefix):
            continue
        relative_path = path.removeprefix(service_root_prefix)
        if relative_path.startswith((f"{ansible_dir}/", f"{terraform_dir}/")):
            continue
        if relative_path in {"README.md", "traefik.yml", "traefik.yml.j2"}:
            continue
        return True

    shared_inventory_prefix = "shared/ansible/inventory/"
    for path in changed_files:
        if (
            path.startswith("shared/ansible/tasks/")
            and shared_dependencies
            and any(
                path_matches_shared_dependency(path, dependency)
                for dependency in shared_dependencies
            )
        ):
            return True
        if path.startswith("shared/ansible/roles/"):
            relative_role_path = path.removeprefix("shared/ansible/roles/")
            role_name = relative_role_path.split("/", 1)[0]
            if service in role_service_index.get(role_name, set()):
                return True
        elif path.startswith(shared_inventory_prefix):
            relative_inventory_path = path.removeprefix(shared_inventory_prefix)
            if relative_inventory_path.startswith("group_vars/"):
                return True
            if relative_inventory_path in {"netbox.yml", "inventory.yml"}:
                return True
            if relative_inventory_path == f"host_vars/{ansible_limit}.yml":
                return True
    return False


def build_jobs(
    config: dict[str, Any],
    changed_files: list[str],
    forced_services: set[str] | None = None,
) -> list[dict[str, Any]]:
    defaults = config["defaults"]
    service_overrides = config["services"]
    platform = read_platform(config)
    traefik_dns_targets = platform.get("dns", {}).get("traefik_dns_targets", [])
    if not isinstance(traefik_dns_targets, list) or not all(
        isinstance(target, str) and target.strip() for target in traefik_dns_targets
    ):
        raise SystemExit("ERROR: dns.traefik_dns_targets must be a list of non-empty DNS targets.")
    services_dir = REPO_ROOT / defaults.get("services_dir", "services")
    terraform_dir = defaults.get("terraform_dir", "terraform")
    ansible_dir = defaults.get("ansible_dir", "ansible")

    platform_config = defaults.get("platform_config", "environments/example/platform.yaml")
    platform_changed = platform_config in changed_files
    contours = rollout_contours(platform)
    deployment_target = deployment_target_from_environment()
    if deployment_target:
        target_service, target_cluster, target_contour = deployment_target
        normalized_contour = next(
            (contour for contour in contours if contour.lower() == target_contour.lower()),
            None,
        )
        if normalized_contour is None:
            raise SystemExit(
                f"ERROR: deployment target uses unknown contour {target_contour!r}."
            )
        deployment_target = (target_service, target_cluster, normalized_contour)
    jobs: list[dict[str, Any]] = []
    route_changed_services: set[str] = set()
    forced_services = forced_services or set()
    services = discover_services(services_dir)
    platform_jobs = openstack_platform_jobs(platform, changed_files)
    jobs.extend(platform_jobs)
    platform_smoke_needs = (
        ["platform:openstack:example:smoke"] if platform_jobs else []
    )
    playbooks: dict[str, Path] = {}
    for service in services:
        overrides = service_overrides.get(service, {}) or {}
        if not isinstance(overrides, dict):
            raise SystemExit(f"ERROR: Service override for {service!r} must be a mapping.")
        playbook = overrides.get("playbook", f"services/{service}/{ansible_dir}/{service}.yml")
        playbooks[service] = REPO_ROOT / playbook
    role_service_index = build_role_service_index(playbooks, REPO_ROOT)
    shared_dependencies_by_service = {
        service: service_shared_dependencies(
            playbook,
            services_dir / service / terraform_dir,
        )
        for service, playbook in playbooks.items()
    }
    global_pipeline_change = has_global_pipeline_impact(changed_files, role_service_index)
    if deployment_target:
        target_service = deployment_target[0]
        if target_service not in services:
            raise SystemExit(
                f"ERROR: deployment target service {target_service!r} does not exist."
            )
        services = [target_service]

    for service in services:
        overrides = service_overrides.get(service, {}) or {}
        if not isinstance(overrides, dict):
            raise SystemExit(f"ERROR: Service override for {service!r} must be a mapping.")

        service_root = services_dir / service
        terraform_present = (service_root / terraform_dir).is_dir()
        playbook = overrides.get("playbook", f"services/{service}/{ansible_dir}/{service}.yml")
        ansible_present = (REPO_ROOT / playbook).is_file()
        ansible_limit = overrides.get("ansible_limit", service.replace("_", "-"))
        service_terraform_prefix = f"services/{service}/{terraform_dir}/"
        service_config_path = f"services/{service}/config.yaml"
        shared_dependencies = shared_dependencies_by_service.get(service, set())
        service_config = {}
        if (service_root / "config.yaml").is_file():
            try:
                with (service_root / "config.yaml").open("r", encoding="utf-8") as config_stream:
                    service_config = yaml.safe_load(config_stream) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise SystemExit(f"ERROR: Cannot read {service_config_path}: {exc}") from exc
        if not isinstance(service_config, dict):
            raise SystemExit(f"ERROR: {service_config_path} must be a YAML mapping.")
        pipeline_config = service_config.get("pipeline", {})
        if pipeline_config is not None and not isinstance(pipeline_config, dict):
            raise SystemExit(f"ERROR: {service_config_path}.pipeline must be a mapping.")
        rollout_ansible_limit = (
            pipeline_config.get("rollout_ansible_limit")
            if isinstance(pipeline_config, dict)
            else None
        )
        if rollout_ansible_limit is not None and (
            not isinstance(rollout_ansible_limit, str) or not rollout_ansible_limit.strip()
        ):
            raise SystemExit(
                f"ERROR: {service_config_path}.pipeline.rollout_ansible_limit must be a non-empty string."
            )
        # Hardware-dependent/manual services must not enter an automatic plan
        # with deployment inputs that cannot be inferred from the repository.
        # An operator can still run one explicitly with FORGEJO_SERVICES.
        if (
            isinstance(pipeline_config, dict)
            and pipeline_config.get("managed", True) is False
            and service not in forced_services
        ):
            continue
        rollout_enabled = contour_rollout_enabled(service_config, service_config_path)
        global_service_change = global_pipeline_change and rollout_enabled
        dns_config = service_config.get("dns", {}) if isinstance(service_config, dict) else {}
        dns_reconcile = isinstance(dns_config, dict) and isinstance(dns_config.get("routing"), dict)
        if (
            f"services/{service}/traefik.yml" in changed_files
            or f"services/{service}/traefik.yml.j2" in changed_files
        ):
            route_changed_services.add(service)
        terraform_changed = service_config_path in changed_files or any(
            path.startswith(service_terraform_prefix) for path in changed_files
        )
        shared_terraform_changed = any(
            path.startswith("shared/terraform/")
            and any(
                path_matches_shared_dependency(path, dependency)
                for dependency in shared_dependencies
            )
            for path in changed_files
        )
        terraform_needed = terraform_present and (
            service in forced_services
            or terraform_changed
            or shared_terraform_changed
            or platform_changed
            or global_service_change
        )
        configure_needed = ansible_present and (
            service in forced_services
            or terraform_needed
            or global_service_change
            or ansible_changed_for_service(
                service,
                playbook,
                changed_files,
                ansible_dir,
                terraform_dir,
                ansible_limit,
                role_service_index,
                shared_dependencies,
            )
        )
        if not terraform_needed and not configure_needed:
            continue

        base_job = {
            "kind": "service",
            "service": service,
            "terraform_present": terraform_present,
            "terraform_needed": terraform_needed,
            "configure_needed": configure_needed,
            "playbook": playbook,
            "ansible_limit": ansible_limit,
            "netbox_api": platform.get("netbox", {}).get("url", ""),
            "platform_needs": platform_smoke_needs,
        }

        targets = (
            deployment_targets(service_config, contours)
            if rollout_enabled
            else []
        )
        if deployment_target and service == deployment_target[0]:
            target_cluster = deployment_target[1]
            target_contour = deployment_target[2]
            if target_contour not in contours:
                raise SystemExit(
                    f"ERROR: deployment target uses unknown contour {target_contour!r}."
                )
            if not any(
                target["cluster"] == target_cluster
                and target["contour"].lower() == target_contour.lower()
                for target in targets
            ):
                raise SystemExit(
                    f"ERROR: deployment target {service}:{target_cluster}:{target_contour.lower()} "
                    "is not a configured rollout target."
                )
        if not targets:
            target_jobs = [{
                **base_job,
                "job_id": service,
                "environment_name": f"{service}",
                "state_name": None,
                "deployment_cluster": None,
                "deployment_contour": None,
                "previous_contour_completion": None,
                "rollout_target_tag": None,
                "runner_allowed_tags": [],
                "runner_labels": ["forgejo-host:host"],
            }]
        else:
            contour_rank = {name: index for index, name in enumerate(contours)}
            targets.sort(key=lambda item: (contour_rank[item["contour"]], item["cluster"]))
            previous_by_contour: dict[str, str | None] = {}
            selected_contours = [name for name in contours if any(
                target["contour"] == name for target in targets
            )]
            for index, contour in enumerate(selected_contours):
                previous_by_contour[contour] = (
                    None if index == 0 else f"complete:{service}:{selected_contours[index - 1]}"
                )
            target_jobs = [{
                **base_job,
                "job_id": f"{service}:{target['cluster']}:{target['contour'].lower()}",
                "environment_name": f"{service}/{target['cluster']}/{target['contour']}",
                "state_name": f"{service}-{target['cluster']}-{target['contour'].lower()}",
                "deployment_cluster": target["cluster"],
                "deployment_contour": target["contour"],
                "previous_contour_completion": previous_by_contour[target["contour"]],
                "ansible_limit": (
                    rollout_ansible_limit
                    or f"{service.replace('_', '-')}-{target['cluster']}-{target['contour'].lower()}-*"
                ),
                "rollout_target_tag": f"rollout-target-{target['contour'].lower()}",
                "runner_allowed_tags": [
                    f"rollout-target-{contour.lower()}"
                    for contour in contours
                    if contour != target["contour"]
                ],
                "runner_labels": [
                    "forgejo-host:host",
                    *[
                        f"rollout-target-{contour.lower()}"
                        for contour in contours
                        if contour != target["contour"]
                    ],
                ],
            } for target in targets]

        target_jobs = [
            {
                **target_job,
                "openbao": openbao_job_config(
                    platform, service_config, target_job["deployment_cluster"]
                ),
            }
            for target_job in target_jobs
        ]

        jobs.append({**target_jobs[0], "template": "validate.yml.j2"})
        for target_job in target_jobs:
            if terraform_needed:
                jobs.append({**target_job, "template": "plan.yml.j2"})
            jobs.append({**target_job, "template": "approve.yml.j2"})
            if terraform_needed:
                jobs.append({**target_job, "template": "apply.yml.j2"})
            if configure_needed:
                jobs.append({**target_job, "template": "configure.yml.j2"})
                if dns_reconcile:
                    dns_job = {
                        **target_job,
                        "template": "dns-reconcile.yml.j2",
                    }
                    if dns_config["routing"].get("mode") == "ingress":
                        if not traefik_dns_targets:
                            raise SystemExit("ERROR: dns.traefik_dns_targets is required for ingress DNS reconciliation.")
                        dns_job["traefik_dns_targets"] = traefik_dns_targets
                        dns_job["dns_stage"] = "dns_ingress"
                        dns_job["dns_needs"] = ["traefik-apply"]
                    jobs.append(dns_job)

        if targets:
            for contour in contours:
                contour_jobs = [job for job in target_jobs if job["deployment_contour"] == contour]
                if contour_jobs:
                    completion_needs = [
                        (
                            f"dns-reconcile:{job['job_id']}"
                            if (
                                configure_needed and dns_reconcile and
                                service_config.get("dns", {}).get("routing", {}).get("mode") != "ingress"
                            )
                            else (f"configure:{job['job_id']}" if configure_needed else f"apply:{job['job_id']}")
                        )
                        for job in contour_jobs
                    ]
                    jobs.append({
                        **base_job,
                        "template": "contour_complete.yml.j2",
                        "contour": contour,
                        "completion_needs": completion_needs,
                        "rollout_target_tag": f"rollout-target-{contour.lower()}",
                    })

            if (
                service_config.get("service", {}).get("topology") in {
                    "active-passive",
                    "etcd-active-passive",
                }
                and len({job["deployment_contour"] for job in target_jobs}) > 1
                and ansible_present
                and configure_needed
            ):
                final_target = target_jobs[-1]
                final_contour = final_target["deployment_contour"]
                jobs.append({
                    **final_target,
                    "job_id": f"{service}:ha-sync:{final_target['deployment_cluster']}",
                    "environment_name": f"{service}/{final_target['deployment_cluster']}/ha-sync",
                    "state_name": f"{service}-{final_target['deployment_cluster']}-ha-sync",
                    "template": "ha_sync.yml.j2",
                    "ansible_limit": f"{service.replace('_', '-')}-{final_target['deployment_cluster']}-*",
                    "ha_sync_needs": [f"complete:{service}:{final_contour}"],
                    "terraform_needed": False,
                    "configure_needed": True,
                })

    powerdns_configure_jobs = [
        job
        for job in jobs
        if job.get("service") == "powerdns" and job.get("template") == "configure.yml.j2"
    ]
    if powerdns_configure_jobs:
        powerdns_dns_needs = sorted(
            f"configure:{job['job_id']}" for job in powerdns_configure_jobs
        )
        powerdns_dns_job = {
            **powerdns_configure_jobs[0],
            "powerdns_dns_needs": powerdns_dns_needs,
        }
        jobs.append({**powerdns_dns_job, "template": "powerdns-dns-preview.yml.j2"})
        jobs.append({**powerdns_dns_job, "template": "powerdns-dns-approve.yml.j2"})
        jobs.append({**powerdns_dns_job, "template": "powerdns-dns-apply.yml.j2"})

    changed_services = sorted({job["service"] for job in jobs if "service" in job})
    traefik_needed = bool(changed_services or route_changed_services or
                          "services/traefik/traefik.yml" in changed_files or
                          any(path in changed_files for path in (
                              "services/traefik/external-services.yml",
                              "services/traefik/external-services.yml.example",
                          )))
    if traefik_needed:
        traefik_config_path = services_dir / "traefik" / "config.yaml"
        try:
            with traefik_config_path.open("r", encoding="utf-8") as config_stream:
                traefik_config = yaml.safe_load(config_stream) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SystemExit(f"ERROR: Cannot read {traefik_config_path.relative_to(REPO_ROOT)}: {exc}") from exc
        if not isinstance(traefik_config, dict):
            raise SystemExit(f"ERROR: {traefik_config_path.relative_to(REPO_ROOT)} must be a YAML mapping.")
        traefik_openbao = openbao_job_config(platform, traefik_config, None)
        traefik_netbox_api = platform.get("netbox", {}).get("url", "")

        jobs.append({
            "service": "traefik",
            "job_id": "traefik:validate",
            "environment_name": "traefik",
            "state_name": "traefik",
            "playbook": "services/traefik/ansible/traefik-config.yml",
            "ansible_limit": "all",
            "template": "traefik-validate.yml.j2",
            "openbao": traefik_openbao,
        })
        traefik_needs = []
        for service in changed_services:
            service_jobs = [job for job in jobs if job.get("service") == service]
            configure_jobs = [job for job in service_jobs if job.get("template") == "configure.yml.j2"]
            apply_jobs = [job for job in service_jobs if job.get("template") == "apply.yml.j2"]
            approve_jobs = [job for job in service_jobs if job.get("template") == "approve.yml.j2"]
            if configure_jobs:
                traefik_needs.extend(f"configure:{job['job_id']}" for job in configure_jobs)
            elif apply_jobs:
                traefik_needs.extend(f"apply:{job['job_id']}" for job in apply_jobs)
            else:
                traefik_needs.extend(f"approve:{job['job_id']}" for job in approve_jobs)
        jobs.append({
            "service": "traefik",
            "job_id": "traefik:preview",
            "environment_name": "traefik",
            "state_name": "traefik",
            "playbook": "services/traefik/ansible/traefik-config.yml",
            "ansible_limit": "all",
            "template": "traefik-preview.yml.j2",
            "traefik_needs": sorted(set(traefik_needs)),
            "openbao": traefik_openbao,
            "netbox_api": traefik_netbox_api,
        })
        jobs.append({
            "service": "traefik",
            "job_id": "traefik:approve",
            "environment_name": "traefik",
            "state_name": "traefik",
            "template": "traefik-approve.yml.j2",
            "traefik_needs": sorted(set(traefik_needs)),
            "openbao": traefik_openbao,
        })
        jobs.append({
            "service": "traefik",
            "job_id": "traefik:apply",
            "environment_name": "traefik",
            "state_name": "traefik",
            "playbook": "services/traefik/ansible/traefik-config.yml",
            "ansible_limit": "all",
            "template": "traefik-apply.yml.j2",
            "traefik_needs": sorted(set(traefik_needs)),
            "openbao": traefik_openbao,
            "netbox_api": traefik_netbox_api,
        })
    return jobs


def forgejo_manifest(config: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a stable, workflow-engine-neutral representation of the plan."""
    deploy_environment = os.environ.get(
        "DEPLOY_ENVIRONMENT",
        os.environ.get("ENVIRONMENT", config["defaults"].get("default_environment", "production")),
    )
    platform = read_platform(config)
    openbao = platform.get("openbao", {})
    forgejo_auth_path = openbao.get("forgejo_auth_path", "forgejo-actions") if isinstance(openbao, dict) else "forgejo-actions"
    forgejo_auth_role = openbao.get("forgejo_auth_role", "ci") if isinstance(openbao, dict) else "ci"
    forgejo_audience = openbao.get("forgejo_audience", "https://openbao.example.test") if isinstance(openbao, dict) else "https://openbao.example.test"
    forgejo_jobs: list[dict[str, Any]] = []
    environment_slug = slugify_environment(deploy_environment)
    for job in jobs:
        forgejo_job = dict(job)
        forgejo_job["deploy_environment"] = deploy_environment
        forgejo_job["state_name"] = f"{environment_slug}-{job.get('state_name') or job.get('service', '')}"
        job_openbao = forgejo_job.get("openbao")
        if isinstance(job_openbao, dict):
            job_openbao = dict(job_openbao)
            job_openbao.update(
                {
                    "auth_path": forgejo_auth_path,
                    "auth_role": forgejo_auth_role,
                    "audience": forgejo_audience,
                }
            )
            forgejo_job["openbao"] = job_openbao
        forgejo_jobs.append(forgejo_job)
    return {
        "version": 1,
        "provider": "forgejo",
        "repository": os.environ.get(
            "FORGEJO_REPOSITORY", os.environ.get("CI_REPO", "example-org/drg")
        ),
        "commit": os.environ.get("FORGEJO_SHA", os.environ.get("CI_COMMIT_SHA", "")),
        "environment": deploy_environment,
        "environment_slug": environment_slug,
        "jobs": [
            {
                **job,
                "order": index,
            }
            for index, job in enumerate(forgejo_jobs)
        ],
        "rollout_contours": rollout_contours(platform),
    }


def render_pipeline(config: dict[str, Any], jobs: list[dict[str, Any]]) -> str:
    defaults = config["defaults"]
    deploy_environment = os.environ.get(
        "DEPLOY_ENVIRONMENT",
        os.environ.get("ENVIRONMENT", defaults.get("default_environment", "production")),
    )

    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = environment.get_template("wrapper.yml.j2")
    default_stages = defaults.get("stages", ["plan", "approve", "apply", "configure"])
    target_contours = [
        contour for contour in rollout_contours(read_platform(config))
        if any(job.get("deployment_contour") == contour for job in jobs)
    ]
    contour_stages = [
        stage
        for contour in target_contours
        for stage in (
            f"plan_{contour.lower()}",
            f"approve_{contour.lower()}",
            f"apply_{contour.lower()}",
            f"configure_{contour.lower()}",
        )
    ]
    post_contour_stages = {
        "ha_sync",
        "powerdns_dns_preview",
        "powerdns_dns_approve",
        "powerdns_dns_apply",
        "traefik_preview",
        "traefik_approve",
        "traefik_apply",
    }
    configured_stages = [stage for stage in default_stages if stage != "validate"]
    platform_stages = [
        "platform_validate",
        "platform_host_preflight",
        "platform_host_apply",
        "platform_kolla_prechecks",
        "platform_kolla_deploy",
        "platform_foundation_plan",
        "platform_foundation_apply",
        "platform_smoke",
    ]
    stages = ["validate"] + platform_stages + [
        stage for stage in configured_stages if stage not in post_contour_stages
    ] + contour_stages + [
        stage for stage in configured_stages if stage in post_contour_stages
    ]
    if any(job.get("dns_stage") == "dns_ingress" for job in jobs):
        stages.append("dns_ingress")
    return template.render(
        include_template=defaults.get("include_template", "shared/templates/gitlab-ci-template.yml"),
        stages=stages,
        child_pipeline_sources=defaults.get("child_pipeline_sources", ["parent_pipeline", "pipeline"]),
        deploy_environment=deploy_environment,
        deploy_environment_slug=slugify_environment(deploy_environment),
        jobs=jobs,
        traefik_needs=next((job.get("traefik_needs", []) for job in jobs if job.get("template") == "traefik-preview.yml.j2"), []),
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate CI pipeline data.")
    parser.add_argument(
        "--format",
        choices=("gitlab", "forgejo-manifest"),
        default="gitlab",
        help="Output format; GitLab remains the default during coexistence.",
    )
    args = parser.parse_args()
    config = read_config()
    changed_files = changed_files_from_environment()
    forced_services = {
        service.strip()
        for service in os.environ.get("FORGEJO_SERVICES", "").split(",")
        if service.strip()
    }
    jobs = build_jobs(config, changed_files, forced_services)
    if args.format == "forgejo-manifest":
        print(json.dumps(forgejo_manifest(config, jobs), indent=2, sort_keys=True), end="\n")
    else:
        print(render_pipeline(config, jobs), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
