#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate repository-owned YAML, Jinja syntax, and generated CI definitions."""

from __future__ import annotations

import subprocess
import sys
import os
import re
from pathlib import Path

import yaml
from jinja2 import Environment, TemplateSyntaxError


REPO_ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_DIRS = {".git", ".terraform", "__pycache__"}
YAMLLINT_CONFIG = REPO_ROOT / ".yamllint.yaml"
OPENBAO_CONFIG_ROOT = REPO_ROOT / "services" / "openbao" / "openbao-config"


def repository_files(*suffixes: str) -> list[Path]:
    paths: list[Path] = []
    for directory, directories, filenames in os.walk(REPO_ROOT):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRS]
        root = Path(directory)
        paths.extend(root / name for name in filenames if (root / name).suffix in suffixes)
    return sorted(paths)


def run_yamllint(*paths: str, content: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        import yamllint  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "yamllint is required for repository validation; install it before running this script."
        ) from exc

    command = [
        sys.executable,
        "-m",
        "yamllint",
        "--config-file",
        str(YAMLLINT_CONFIG),
        "--format",
        "parsable",
        *(paths or ("-",)),
    ]
    if content is None:
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        input=content.encode("utf-8"),
        capture_output=True,
    )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8"),
        result.stderr.decode("utf-8"),
    )


def validate_yaml() -> None:
    paths = repository_files(".yml", ".yaml")
    result = run_yamllint(*(str(path) for path in paths))
    if result.returncode != 0:
        raise SystemExit(result.stdout or result.stderr)

    for path in paths:
        try:
            list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            raise SystemExit(f"Invalid YAML in {path.relative_to(REPO_ROOT)}: {exc}") from exc


def validate_yaml_text(content: str, description: str) -> None:
    result = run_yamllint(content=content.replace("\r\n", "\n").replace("\r", "\n"))
    if result.returncode != 0:
        raise SystemExit(f"Invalid YAML in {description}:\n{result.stdout or result.stderr}")


def validate_jinja() -> None:
    environment = Environment()
    for path in repository_files(".j2"):
        try:
            environment.parse(path.read_text(encoding="utf-8"))
        except TemplateSyntaxError as exc:
            raise SystemExit(f"Invalid Jinja in {path.relative_to(REPO_ROOT)}: {exc}") from exc


def validate_openbao_configuration() -> None:
    """Validate repository-owned OpenBao declarations before any API call."""
    if not OPENBAO_CONFIG_ROOT.is_dir():
        return

    required_directories = ("auth", "policies", "roles", "secrets-engines")
    for name in required_directories:
        if not (OPENBAO_CONFIG_ROOT / name).is_dir():
            raise SystemExit(f"OpenBao configuration directory is missing: openbao-config/{name}")

    def documents(directory: str) -> list[tuple[Path, dict]]:
        result: list[tuple[Path, dict]] = []
        for path in sorted((OPENBAO_CONFIG_ROOT / directory).glob("*.yaml")):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise SystemExit(f"Cannot read {path.relative_to(REPO_ROOT)}: {exc}") from exc
            if not isinstance(document, dict):
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} must be a YAML mapping.")
            result.append((path, document))
        return result

    def reject_secret_values(value: object, path: Path, key: str = "") -> None:
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if not isinstance(nested_key, str):
                    raise SystemExit(f"{path.relative_to(REPO_ROOT)} contains a non-string key.")
                lowered = nested_key.lower()
                is_env_reference = lowered.endswith("_env")
                forbidden = re.search(r"(^|_)(password|private_key|secret|token)($|_)", lowered)
                if forbidden and not is_env_reference and lowered not in {
                    "token_ttl",
                    "token_max_ttl",
                    "token_policies",
                }:
                    raise SystemExit(
                        f"{path.relative_to(REPO_ROOT)} must not contain secret field {nested_key!r}; "
                        "store only an environment-variable name or public reference."
                    )
                reject_secret_values(nested_value, path, nested_key)
        elif isinstance(value, list):
            for item in value:
                reject_secret_values(item, path, key)

    policy_documents = documents("policies")
    policy_names: set[str] = set()
    valid_capabilities = {"create", "read", "update", "delete", "list", "patch", "sudo", "deny"}
    declared_mounts = {"auth", "sys"}
    for path, document in documents("secrets-engines"):
        reject_secret_values(document, path)
        mount = document.get("mount")
        engine_type = document.get("type")
        if not isinstance(mount, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", mount):
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} requires a lower-case mount name.")
        if not isinstance(engine_type, str) or not engine_type:
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} requires a secrets-engine type.")
        if mount in declared_mounts:
            raise SystemExit(f"Duplicate OpenBao mount {mount!r} in {path.relative_to(REPO_ROOT)}.")
        declared_mounts.add(mount)

    for path, document in policy_documents:
        reject_secret_values(document, path)
        name = document.get("name")
        rules = document.get("rules")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} requires a lower-case policy name.")
        if name in policy_names:
            raise SystemExit(f"Duplicate OpenBao policy {name!r}.")
        policy_names.add(name)
        if not isinstance(rules, list) or not rules:
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} requires a non-empty rules list.")
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} has an invalid policy rule.")
            capabilities = rule.get("capabilities")
            if not isinstance(capabilities, list) or not set(capabilities) <= valid_capabilities:
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} has invalid policy capabilities.")
            policy_path = rule["path"]
            mount = policy_path.split("/", 1)[0]
            if mount not in declared_mounts and policy_path != "*":
                raise SystemExit(
                    f"{path.relative_to(REPO_ROOT)} references undeclared OpenBao mount {mount!r}."
                )
            if name != "admin" and (policy_path == "*" or "*" in capabilities):
                raise SystemExit(f"Only the admin policy may contain global wildcard access: {path.relative_to(REPO_ROOT)}")

    auth_names: set[str] = set()
    for path, document in documents("auth"):
        reject_secret_values(document, path)
        providers = document.get("providers")
        definitions = providers if providers is not None else [document]
        if not isinstance(definitions, list):
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} providers must be a list.")
        for definition in definitions:
            if not isinstance(definition, dict):
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} contains an invalid auth definition.")
            mount = definition.get("mount")
            auth_type = definition.get("type")
            if not isinstance(mount, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", mount):
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} requires a lower-case auth mount name.")
            if mount in auth_names:
                raise SystemExit(f"Duplicate OpenBao auth mount {mount!r}.")
            auth_names.add(mount)
            if auth_type not in {"cert", "jwt", "oidc"}:
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} has unsupported auth type {auth_type!r}.")
            if auth_type == "cert":
                ca_cert_file = definition.get("ca_cert_file")
                if not isinstance(ca_cert_file, str) or not (OPENBAO_CONFIG_ROOT / ca_cert_file).is_file():
                    raise SystemExit(f"{path.relative_to(REPO_ROOT)} must reference an existing public CA file.")
            for role in definition.get("roles", []):
                if not isinstance(role, dict):
                    raise SystemExit(f"{path.relative_to(REPO_ROOT)} has an invalid auth role.")
                for policy in role.get("policies", role.get("token_policies", [])):
                    if policy not in policy_names:
                        raise SystemExit(f"{path.relative_to(REPO_ROOT)} references unknown policy {policy!r}.")

    for path, document in documents("roles"):
        reject_secret_values(document, path)
        policy = document.get("policy")
        auth_mount = document.get("auth_mount")
        if policy not in policy_names or auth_mount not in auth_names:
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} must reference declared policy and auth mount.")


def validate_logging_integration() -> None:
    services_dir = REPO_ROOT / "services"
    missing_roles: list[str] = []
    invalid_units: list[str] = []

    for service_dir in sorted(path for path in services_dir.iterdir() if path.is_dir()):
        config_path = service_dir / "config.yaml"
        playbook_path = service_dir / "ansible" / f"{service_dir.name}.yml"
        if not config_path.is_file() or not playbook_path.is_file():
            continue

        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            playbook = yaml.safe_load(playbook_path.read_text(encoding="utf-8")) or []
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise SystemExit(
                f"Unable to inspect logging integration for {service_dir.name}: {exc}"
            ) from exc

        roles = {
            role
            for play in playbook
            if isinstance(play, dict)
            for role in (play.get("roles", []) or [])
            if isinstance(role, str)
        }
        if "logging_agent" not in roles:
            missing_roles.append(str(playbook_path.relative_to(REPO_ROOT)))

        logging_config = config.get("logging", {}) if isinstance(config, dict) else {}
        units = logging_config.get("journal_units", []) if isinstance(logging_config, dict) else []
        if not isinstance(units, list):
            invalid_units.append(f"{config_path.relative_to(REPO_ROOT)}: logging.journal_units must be a list")
            continue
        for unit in units:
            if not isinstance(unit, str) or not unit or unit.endswith(".service"):
                invalid_units.append(
                    f"{config_path.relative_to(REPO_ROOT)}: invalid journal unit {unit!r}"
                )

    if missing_roles:
        raise SystemExit(
            "Every runtime service playbook must include logging_agent:\n"
            + "\n".join(f"- {path}" for path in missing_roles)
        )
    if invalid_units:
        raise SystemExit(
            "Journal units must be non-empty basenames without the .service suffix:\n"
            + "\n".join(f"- {entry}" for entry in invalid_units)
        )


def validate_generated_pipeline() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "shared/scripts/generate_pipeline.py"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()
        raise SystemExit(
            f"Pipeline generation failed with exit code {exc.returncode}"
            + (f":\n{details}" if details else ".")
        ) from exc
    validate_yaml_text(result.stdout, "generated pipeline")
    try:
        document = yaml.safe_load(result.stdout)
    except yaml.YAMLError as exc:
        raise SystemExit(f"Generated pipeline is invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise SystemExit("Generated pipeline must be a YAML mapping.")


def validate_generated_forgejo_manifest() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "shared/scripts/generate_pipeline.py", "--format", "forgejo-manifest"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        document = yaml.safe_load(result.stdout)
    except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
        raise SystemExit(f"Forgejo manifest generation failed: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise SystemExit("Generated Forgejo manifest must be a version 1 mapping.")
    if document.get("provider") != "forgejo" or not isinstance(document.get("jobs"), list):
        raise SystemExit("Generated Forgejo manifest has an invalid provider or jobs list.")
    for job in document["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("template"), str):
            raise SystemExit("Generated Forgejo manifest contains an invalid job.")
        labels = job.get("runner_labels", [])
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise SystemExit("Generated Forgejo manifest contains invalid runner labels.")


def validate_forgejo_workflows() -> None:
    workflow_root = REPO_ROOT / ".forgejo" / "workflows"
    if not workflow_root.is_dir():
        raise SystemExit("Forgejo workflow directory is missing.")
    for path in sorted(workflow_root.glob("*.y*ml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise SystemExit(f"Invalid Forgejo workflow {path.relative_to(REPO_ROOT)}: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
            raise SystemExit(f"{path.relative_to(REPO_ROOT)} must define a jobs mapping.")
        for job_name, job in document["jobs"].items():
            if not isinstance(job, dict):
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} job {job_name!r} is invalid.")
            if "tags" in job or "when" in job or "trigger" in job:
                raise SystemExit(f"{path.relative_to(REPO_ROOT)} job {job_name!r} contains GitLab syntax.")


def validate_generated_docker_pipeline() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "shared/scripts/docker/generate_docker_utils.py"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()
        raise SystemExit(
            f"Docker pipeline generation failed with exit code {exc.returncode}"
            + (f":\n{details}" if details else ".")
        ) from exc
    validate_yaml_text(result.stdout, "generated Docker pipeline")


def validate_generated_state_migration_pipeline() -> None:
    result = subprocess.run(
        [sys.executable, "shared/scripts/generate_terraform_state_migration.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    validate_yaml_text(result.stdout, "generated Terraform state migration pipeline")
    try:
        document = yaml.safe_load(result.stdout)
    except yaml.YAMLError as exc:
        raise SystemExit(f"Generated Terraform state migration pipeline is invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise SystemExit("Generated Terraform state migration pipeline must be a YAML mapping.")


def main() -> int:
    validate_yaml()
    validate_jinja()
    validate_openbao_configuration()
    validate_logging_integration()
    validate_generated_pipeline()
    validate_generated_forgejo_manifest()
    validate_forgejo_workflows()
    validate_generated_docker_pipeline()
    validate_generated_state_migration_pipeline()
    print("Repository validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
