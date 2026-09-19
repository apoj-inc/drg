#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the human-maintained OpenStack YAML schema into Terraform roots."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "foundation"
PROJECTS_DIR = FOUNDATION / "projects"
TEMPLATES = FOUNDATION / "templates"

TARGETS = {
    "identity": "identity/main.tf.j2",
    "network": "network/main.tf.j2",
    "security": "security/main.tf.j2",
    "compute-catalog": "compute-catalog/main.tf.j2",
    "images": "images/main.tf.j2",
}
GENERATED_FILES = ("main.tf", "versions.tf", "providers.tf", "variables.tf", "outputs.tf")


def tf_string(value: Any) -> str:
    return json.dumps(str(value))


def tf_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, (int, float)):
        return str(value)
    return tf_string(value)


def size_in_mb(value: Any) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(MB|GB|TB)\s*", str(value), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid size {value!r}; use a value such as 512MB, 2GB or 1TB")
    number = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {"MB": 1, "GB": 1024, "TB": 1024 * 1024}[unit]
    result = number * multiplier
    if result != int(result):
        raise ValueError(f"Size {value!r} does not resolve to a whole MB")
    return int(result)


def size_in_gb(value: Any) -> int:
    megabytes = size_in_mb(value)
    if megabytes % 1024 != 0:
        raise ValueError(f"Size {value!r} must resolve to a whole GB for disk sizes")
    return megabytes // 1024


def load_schema() -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for path in sorted(FOUNDATION.glob("*.yaml")):
        with path.open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream) or {}
        if not isinstance(document, dict):
            raise ValueError(f"Schema document must be a mapping: {path}")
        for key, value in document.items():
            if key in schema:
                raise ValueError(f"Duplicate top-level section {key!r}: {path}")
            schema[key] = value
    required = {"provider"}
    missing = sorted(required - schema.keys())
    if missing:
        raise ValueError(f"Missing foundation sections: {', '.join(missing)}")
    projects = {}
    for project_dir in sorted(PROJECTS_DIR.iterdir() if PROJECTS_DIR.is_dir() else []):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        project = {}
        for path in sorted(project_dir.glob("*.yaml")):
            with path.open(encoding="utf-8") as stream:
                document = yaml.safe_load(stream) or {}
            if not isinstance(document, dict):
                raise ValueError(f"Project schema document must be a mapping: {path}")
            for key, value in document.items():
                if key in project:
                    raise ValueError(f"Duplicate project section {key!r}: {path}")
                project[key] = value
        projects[project_dir.name] = project
    identity_projects = {}
    for project_name, project in projects.items():
        identity = project.get("identity", {})
        if not isinstance(identity, dict) or "projects" in identity:
            raise ValueError(f"{project_name}/identity.yaml must define project settings directly")
        identity_projects[project_name] = identity
    if not identity_projects:
        raise ValueError("project identity.yaml files must define at least one project")
    schema["identity"] = {"projects": identity_projects}
    selected_project = os.environ.get("OPENSTACK_FOUNDATION_PROJECT", "").strip()
    if not selected_project:
        if len(projects) != 1:
            raise ValueError(
                "OPENSTACK_FOUNDATION_PROJECT is required when multiple projects are configured"
            )
        selected_project = next(iter(projects))
    if selected_project not in projects:
        raise ValueError(f"Unknown foundation project: {selected_project}")
    for key, value in projects[selected_project].items():
        if key == "identity":
            continue
        if key in schema:
            raise ValueError(f"Duplicate foundation section {key!r} from project directory")
        schema[key] = value
    schema["projects"] = projects
    schema["project_name"] = selected_project
    return schema


def render(schema: dict[str, Any], target: str, filename: str) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    environment.filters["tf_string"] = tf_string
    environment.filters["tf_value"] = tf_value
    environment.filters["size_to_mb"] = size_in_mb
    environment.filters["size_to_gb"] = size_in_gb
    template_name = TARGETS[target] if filename == "main.tf" else f"_{filename}.j2"
    rendered = environment.get_template(template_name).render(config=schema, target=target)
    tofu = shutil.which("tofu")
    if tofu:
        result = subprocess.run(
            [tofu, "fmt", "-"],
            input=rendered,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files differ")
    args = parser.parse_args()
    schema = load_schema()
    project_name = schema["project_name"]
    project_root = PROJECTS_DIR / project_name
    changed = False
    for target in TARGETS:
        (project_root / target).mkdir(parents=True, exist_ok=True)
        for filename in GENERATED_FILES:
            destination = project_root / target / filename
            rendered = render(schema, target, filename)
            # Generated files are intentionally not tracked. --check validates
            # only an existing workspace copy, while a clean checkout remains
            # valid before the normal generation step.
            # A clean CI checkout has the target directories but none of the
            # generated Terraform files. Treat a missing file as out of date;
            # otherwise the first generation pass would incorrectly do
            # nothing and OpenTofu would see an empty root.
            current = destination.read_text(encoding="utf-8") if destination.is_file() else None
            if current != rendered:
                changed = True
                if args.check:
                    print(f"out of date: {destination.relative_to(ROOT)}")
                else:
                    destination.write_text(rendered, encoding="utf-8")
                    print(f"generated: {destination.relative_to(ROOT)}")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
