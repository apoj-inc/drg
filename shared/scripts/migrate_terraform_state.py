#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Migrate Terraform/OpenTofu state from GitLab's HTTP backend to PostgreSQL.

The migration deliberately uses temporary directories containing only a backend
configuration.  It therefore works even when the checked-in Terraform root
already points at PostgreSQL and never rewrites tracked files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
NO_STATE_MARKERS = (
    "no state file was found",
    "state snapshot was not found",
    "state was not found",
    "backend state was not found",
)


class MigrationError(RuntimeError):
    """A user-actionable migration failure."""


@dataclass(frozen=True)
class MigrationState:
    name: str
    schema_name: str


@dataclass
class PreparedState:
    state: MigrationState
    state_json: str
    state_hash: str
    source_summary: dict[str, Any]
    destination_dir: Path
    destination_exists: bool


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    tofu_bin: str
    mode: str
    allow_overwrite: bool
    confirmation: str
    report_path: Path
    gitlab_api_url: str
    gitlab_project_id: str
    gitlab_username: str
    gitlab_password: str
    pg_conn_str: str
    pg_password: str
    pg_schema_prefix: str


def parse_bool(value: str, name: str, default: bool = False) -> bool:
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MigrationError(f"{name} must be true or false, got {value!r}.")


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def slugify_environment(environment: str) -> str:
    slug = environment.lower()
    slug = re.sub(r"[^a-z0-9]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "production"


def schema_name(state_name: str, prefix: str = "terraform_") -> str:
    """Match the schema naming used by shared/templates/gitlab-ci-template.yml."""

    return prefix + re.sub(r"[^A-Za-z0-9_]", "_", state_name)


def state_names_from_services(
    services: list[str], environment: str, repo_root: Path = REPO_ROOT
) -> list[str]:
    if not services:
        return []

    platform_path = repo_root / "environments" / "example" / "platform.yaml"
    try:
        platform = yaml.safe_load(platform_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError(f"Cannot read {platform_path}: {exc}") from exc

    if not isinstance(platform, dict):
        raise MigrationError("Platform configuration must be a YAML mapping.")

    contour_config = platform.get("rollout_contours", {})
    if not isinstance(contour_config, dict):
        raise MigrationError("platform.rollout_contours must be a mapping.")
    contours = [
        name
        for name, value in sorted(
            contour_config.items(), key=lambda item: item[1].get("order", 0)
        )
        if isinstance(name, str) and isinstance(value, dict)
    ]

    result: list[str] = []
    environment_slug = slugify_environment(environment)
    for service in services:
        service_root = repo_root / "services" / service
        terraform_root = service_root / "terraform"
        if not terraform_root.is_dir():
            raise MigrationError(f"Service {service!r} has no Terraform root at {terraform_root}.")

        config_path = service_root / "config.yaml"
        if not config_path.is_file():
            result.append(f"{environment_slug}-{service}")
            continue
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise MigrationError(f"Cannot read {config_path}: {exc}") from exc
        if not isinstance(config, dict):
            raise MigrationError(f"{config_path} must contain a YAML mapping.")

        rollout = config.get("rollout") or {}
        if not isinstance(rollout, dict):
            raise MigrationError(f"{config_path}: rollout must be a mapping.")
        if not rollout.get("by_contour", False):
            result.append(f"{environment_slug}-{service}")
            continue

        placement = config.get("placement") or {}
        clusters = placement.get("clusters", {}) if isinstance(placement, dict) else {}
        if not isinstance(clusters, dict):
            raise MigrationError(f"{config_path}: placement.clusters must be a mapping.")

        service_targets: list[str] = []
        for cluster, cluster_config in clusters.items():
            if not isinstance(cluster, str) or not isinstance(cluster_config, dict):
                raise MigrationError(f"{config_path}: invalid placement cluster {cluster!r}.")
            nodes = cluster_config.get("nodes", {})
            if not isinstance(nodes, dict):
                raise MigrationError(f"{config_path}: {cluster}.nodes must be a mapping.")
            active_contours: set[str] = set()
            for node_config in nodes.values():
                if not isinstance(node_config, dict):
                    raise MigrationError(f"{config_path}: node configuration must be a mapping.")
                replica_counts = node_config.get("replicas_by_contour", {})
                if not isinstance(replica_counts, dict):
                    raise MigrationError(f"{config_path}: replica counts must be a mapping.")
                for contour, replicas in replica_counts.items():
                    if replicas and contour not in contours:
                        raise MigrationError(
                            f"{config_path}: contour {contour!r} is not defined by the platform."
                        )
                    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
                        raise MigrationError(
                            f"{config_path}: replica count for {cluster}/{contour} must be non-negative."
                        )
                    if replicas > 0:
                        active_contours.add(contour)
            service_targets.extend(
                f"{environment_slug}-{service}-{cluster}-{contour.lower()}"
                for contour in contours
                if contour in active_contours
            )
        result.extend(service_targets)

    return result


def resolve_states(
    explicit_names: list[str],
    services: list[str],
    environment: str,
    repo_root: Path = REPO_ROOT,
    prefix: str = "terraform_",
) -> list[MigrationState]:
    names = [*explicit_names, *state_names_from_services(services, environment, repo_root)]
    if not names:
        raise MigrationError(
            "Set MIGRATION_STATE_NAMES and/or MIGRATION_SERVICES; refusing to select all states implicitly."
        )

    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise MigrationError(f"Duplicate migration state names: {', '.join(duplicates)}.")

    states = [MigrationState(name=name, schema_name=schema_name(name, prefix)) for name in names]
    schemas: dict[str, str] = {}
    for state in states:
        previous = schemas.setdefault(state.schema_name, state.name)
        if previous != state.name:
            raise MigrationError(
                f"State names {previous!r} and {state.name!r} map to the same PostgreSQL schema "
                f"{state.schema_name!r}."
            )
    return states


def canonical_state_hash(state_json: str, *, ignore_serial: bool = False) -> str:
    try:
        document = json.loads(state_json)
    except json.JSONDecodeError as exc:
        raise MigrationError(f"Terraform state is not valid JSON: {exc}") from exc
    if ignore_serial and isinstance(document, dict):
        document = dict(document)
        document.pop("serial", None)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def state_summary(state_json: str) -> dict[str, Any]:
    """Return safe metadata useful for diagnosing a post-write mismatch."""

    try:
        document = json.loads(state_json)
    except json.JSONDecodeError as exc:
        raise MigrationError(f"Terraform state is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise MigrationError("Terraform state must be a JSON object.")

    addresses: list[str] = []
    resources = document.get("resources", [])
    if isinstance(resources, list):
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            module = resource.get("module")
            prefix = f"{module}." if isinstance(module, str) and module else ""
            mode = "data." if resource.get("mode") == "data" else ""
            resource_type = resource.get("type", "")
            name = resource.get("name", "")
            addresses.append(f"{prefix}{mode}{resource_type}.{name}")

    outputs = document.get("outputs", {})
    output_names = sorted(outputs) if isinstance(outputs, dict) else []
    address_fingerprint = hashlib.sha256(
        json.dumps(sorted(addresses), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "serial": document.get("serial"),
        "lineage": document.get("lineage"),
        "content_hash": canonical_state_hash(state_json, ignore_serial=True),
        "terraform_version": document.get("terraform_version"),
        "resource_count": len(resources) if isinstance(resources, list) else 0,
        "resource_address_hash": address_fingerprint,
        "output_names": output_names,
        "top_level_keys": sorted(document),
    }


def write_backend(directory: Path, backend: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "backend.tf").write_text(
        f'terraform {{\n  backend "{backend}" {{}}\n}}\n',
        encoding="utf-8",
    )


def write_local_backend(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "backend.tf").write_text(
        'terraform {\n  backend "local" {\n    path = "normalized.tfstate"\n  }\n}\n',
        encoding="utf-8",
    )


def command_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def run_tofu(
    settings: Settings,
    directory: Path,
    args: list[str],
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    command = [settings.tofu_bin, *args]
    try:
        return runner(
            command,
            cwd=directory,
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise MigrationError(f"Cannot execute {settings.tofu_bin!r}: {exc}") from exc


def require_success(result: subprocess.CompletedProcess[str], operation: str) -> str:
    if result.returncode != 0:
        detail = command_output(result)
        raise MigrationError(f"{operation} failed{': ' + detail if detail else '.'}")
    return result.stdout.strip()


def is_missing_state(result: subprocess.CompletedProcess[str]) -> bool:
    detail = command_output(result).lower()
    return result.returncode != 0 and any(marker in detail for marker in NO_STATE_MARKERS)


def backend_environment(base: Mapping[str, str], **values: str) -> dict[str, str]:
    result = dict(base)
    result.update(values)
    return result


def source_environment(settings: Settings, state_name: str) -> dict[str, str]:
    address = (
        f"{settings.gitlab_api_url.rstrip('/')}/projects/"
        f"{quote(settings.gitlab_project_id, safe='')}/terraform/state/"
        f"{quote(state_name, safe='')}"
    )
    return backend_environment(
        os.environ,
        TF_HTTP_ADDRESS=address,
        TF_HTTP_LOCK_ADDRESS=f"{address}/lock",
        TF_HTTP_UNLOCK_ADDRESS=f"{address}/lock",
        TF_HTTP_USERNAME=settings.gitlab_username,
        TF_HTTP_PASSWORD=settings.gitlab_password,
        TF_HTTP_LOCK_METHOD="POST",
        TF_HTTP_UNLOCK_METHOD="DELETE",
        TF_HTTP_RETRY_WAIT_MIN="5",
    )


def destination_environment(settings: Settings, state: MigrationState) -> dict[str, str]:
    return backend_environment(
        os.environ,
        PG_CONN_STR=settings.pg_conn_str,
        PGPASSWORD=settings.pg_password,
        PG_SCHEMA_NAME=state.schema_name,
    )


def pull_state(
    settings: Settings,
    directory: Path,
    environment: Mapping[str, str],
    operation: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str | None, bool]:
    result = run_tofu(settings, directory, ["state", "pull"], environment, runner)
    if result.returncode == 0:
        state_json = result.stdout.strip()
        if not state_json:
            raise MigrationError(f"{operation} returned an empty state document.")
        return state_json, True
    if is_missing_state(result):
        return None, False
    require_success(result, operation)
    raise AssertionError("unreachable")


def prepare_state(
    settings: Settings,
    state: MigrationState,
    workspace: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PreparedState:
    source_dir = workspace / "source"
    destination_dir = workspace / "destination"
    write_backend(source_dir, "http")
    write_backend(destination_dir, "pg")

    source_env = source_environment(settings, state.name)
    require_success(
        run_tofu(settings, source_dir, ["init", "-reconfigure", "-input=false"], source_env, runner),
        f"Initializing GitLab backend for {state.name!r}",
    )
    state_json, source_exists = pull_state(
        settings, source_dir, source_env, f"Pulling GitLab state {state.name!r}", runner
    )
    if not source_exists or state_json is None:
        raise MigrationError(f"GitLab state {state.name!r} does not exist or is inaccessible.")

    # state push parses and serializes state through OpenTofu's internal state
    # model. Normalize the source through the same round trip before hashing
    # and writing it to PostgreSQL, otherwise harmless JSON ordering/default
    # differences can look like a migration failure.
    normalized_dir = workspace / "normalized"
    write_local_backend(normalized_dir)
    normalized_source_file = normalized_dir / "source-state.json"
    normalized_source_file.write_text(state_json + "\n", encoding="utf-8")
    normalized_source_file.chmod(0o600)
    local_env = dict(os.environ)
    require_success(
        run_tofu(
            settings,
            normalized_dir,
            ["init", "-reconfigure", "-input=false"],
            local_env,
            runner,
        ),
        f"Initializing normalization backend for {state.name!r}",
    )
    require_success(
        run_tofu(
            settings,
            normalized_dir,
            ["state", "push", str(normalized_source_file)],
            local_env,
            runner,
        ),
        f"Normalizing GitLab state {state.name!r}",
    )
    normalized_json, normalized_exists = pull_state(
        settings,
        normalized_dir,
        local_env,
        f"Reading normalized state {state.name!r}",
        runner,
    )
    if not normalized_exists or normalized_json is None:
        raise MigrationError(f"Normalized state {state.name!r} could not be read back.")
    state_json = normalized_json

    destination_env = destination_environment(settings, state)
    require_success(
        run_tofu(
            settings,
            destination_dir,
            ["init", "-reconfigure", "-input=false"],
            destination_env,
            runner,
        ),
        f"Initializing PostgreSQL backend for {state.name!r}",
    )
    destination_json, destination_exists = pull_state(
        settings,
        destination_dir,
        destination_env,
        f"Checking PostgreSQL destination for {state.name!r}",
        runner,
    )
    del destination_json
    if destination_exists and not settings.allow_overwrite:
        raise MigrationError(
            f"PostgreSQL destination for {state.name!r} is not empty. "
            "Set MIGRATION_ALLOW_OVERWRITE=true only after verifying the destination."
        )

    return PreparedState(
        state=state,
        state_json=state_json,
        state_hash=canonical_state_hash(state_json),
        source_summary=state_summary(state_json),
        destination_dir=destination_dir,
        destination_exists=destination_exists,
    )


def apply_state(
    settings: Settings,
    prepared: PreparedState,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    state_file = prepared.destination_dir / "migration-state.json"
    state_file.write_text(prepared.state_json + "\n", encoding="utf-8")
    state_file.chmod(0o600)
    environment = destination_environment(settings, prepared.state)
    args = ["state", "push"]
    if settings.allow_overwrite:
        args.append("-force")
    args.append(str(state_file))
    require_success(
        run_tofu(settings, prepared.destination_dir, args, environment, runner),
        f"Writing PostgreSQL state {prepared.state.name!r}",
    )
    written_json, written_exists = pull_state(
        settings,
        prepared.destination_dir,
        environment,
        f"Verifying PostgreSQL state {prepared.state.name!r}",
        runner,
    )
    if not written_exists or written_json is None:
        raise MigrationError(f"PostgreSQL state {prepared.state.name!r} disappeared after writing.")
    written_hash = canonical_state_hash(written_json)
    written_content_hash = canonical_state_hash(written_json, ignore_serial=True)
    source_content_hash = canonical_state_hash(prepared.state_json, ignore_serial=True)
    if written_content_hash != source_content_hash:
        destination_summary = state_summary(written_json)
        raise MigrationError(
            f"State content mismatch for {prepared.state.name!r}: "
            f"source {prepared.state_hash}, destination {written_hash}. "
            f"Metadata: source={json.dumps(prepared.source_summary, sort_keys=True)}; "
            f"destination={json.dumps(destination_summary, sort_keys=True)}."
        )
    return {
        "destination_hash": written_hash,
        "destination_content_hash": written_content_hash,
        "destination_summary": state_summary(written_json),
    }


def build_settings(environment: Mapping[str, str] | None = None) -> Settings:
    env = dict(environment or os.environ)
    mode = env.get("MIGRATION_MODE", "plan").strip().lower()
    if mode not in {"plan", "apply"}:
        raise MigrationError("MIGRATION_MODE must be plan or apply.")

    repo_root = Path(env.get("MIGRATION_REPO_ROOT", str(REPO_ROOT))).resolve()
    report_path = Path(
        env.get("MIGRATION_REPORT_PATH", str(repo_root / "terraform-state-migration-report.json"))
    ).resolve()
    gitlab_api_url = env.get("GITLAB_API_URL", env.get("CI_API_V4_URL", "")).strip()
    project_id = env.get("GITLAB_PROJECT_ID", env.get("CI_PROJECT_ID", "")).strip()
    gitlab_password = env.get(
        "TF_HTTP_PASSWORD",
        env.get("GITLAB_TERRAFORM_STATE_PASSWORD", env.get("CI_JOB_TOKEN", "")),
    ).strip()
    pg_conn_str = env.get("PG_CONN_STR", "").strip()
    pg_password = env.get("PGPASSWORD", "").strip()
    missing = [
        name
        for name, value in (
            ("GITLAB_API_URL or CI_API_V4_URL", gitlab_api_url),
            ("GITLAB_PROJECT_ID or CI_PROJECT_ID", project_id),
            ("GitLab state password", gitlab_password),
            ("PG_CONN_STR", pg_conn_str),
            ("PGPASSWORD", pg_password),
        )
        if not value
    ]
    if missing:
        raise MigrationError("Missing required migration environment: " + ", ".join(missing) + ".")

    return Settings(
        repo_root=repo_root,
        tofu_bin=env.get("TOFU_BIN", "tofu"),
        mode=mode,
        allow_overwrite=parse_bool(env.get("MIGRATION_ALLOW_OVERWRITE", "false"), "MIGRATION_ALLOW_OVERWRITE"),
        confirmation=env.get("MIGRATION_CONFIRM", ""),
        report_path=report_path,
        gitlab_api_url=gitlab_api_url,
        gitlab_project_id=project_id,
        gitlab_username=env.get(
            "TF_HTTP_USERNAME", env.get("GITLAB_TERRAFORM_STATE_USERNAME", "gitlab-ci-token")
        ),
        gitlab_password=gitlab_password,
        pg_conn_str=pg_conn_str,
        pg_password=pg_password,
        pg_schema_prefix=env.get("PG_SCHEMA_PREFIX", "terraform_"),
    )


def write_report(path: Path, results: list[dict[str, Any]], error: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed" if error else "ok", "results": results}
    if error:
        report["error"] = error
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def migrate(
    settings: Settings,
    states: list[MigrationState],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    if settings.mode == "apply" and settings.confirmation != "MIGRATE":
        raise MigrationError("Apply mode requires MIGRATION_CONFIRM=MIGRATE.")

    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="terraform-state-migration-") as temporary:
            workspace_root = Path(temporary)
            prepared: list[PreparedState] = []
            for index, state in enumerate(states):
                prepared_state = prepare_state(settings, state, workspace_root / str(index), runner)
                prepared.append(prepared_state)
                results.append(
                    {
                        "state": state.name,
                        "schema": state.schema_name,
                        "source_hash": prepared_state.state_hash,
                        "source_content_hash": prepared_state.source_summary["content_hash"],
                        "source_summary": prepared_state.source_summary,
                        "destination_was_present": prepared_state.destination_exists,
                        "status": "ready" if settings.mode == "apply" else "dry-run",
                    }
                )

            if settings.mode == "apply":
                for index, prepared_state in enumerate(prepared):
                    try:
                        verification = apply_state(settings, prepared_state, runner)
                        results[index].update(verification)
                        results[index]["status"] = "migrated"
                    except MigrationError as exc:
                        results[index]["status"] = "failed"
                        results[index]["error"] = str(exc)
                        raise
    except MigrationError as exc:
        write_report(settings.report_path, results, str(exc))
        raise

    write_report(settings.report_path, results)
    for result in results:
        print(
            f"{result['status']}: {result['state']} -> {result['schema']} "
            f"({result['source_hash']})"
        )
    return 0


def main() -> int:
    settings: Settings | None = None
    try:
        settings = build_settings()
        try:
            states = resolve_states(
                parse_csv(os.environ.get("MIGRATION_STATE_NAMES", "")),
                parse_csv(os.environ.get("MIGRATION_SERVICES", "")),
                os.environ.get("MIGRATION_ENVIRONMENT", "production"),
                settings.repo_root,
                settings.pg_schema_prefix,
            )
        except MigrationError as exc:
            write_report(settings.report_path, [], str(exc))
            raise
        return migrate(settings, states)
    except MigrationError as exc:
        if settings is None:
            report_path = Path(
                os.environ.get(
                    "MIGRATION_REPORT_PATH",
                    str(REPO_ROOT / "terraform-state-migration-report.json"),
                )
            ).resolve()
            try:
                write_report(report_path, [], str(exc))
            except OSError as report_error:
                print(f"WARNING: Could not write migration report: {report_error}", file=sys.stderr)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
