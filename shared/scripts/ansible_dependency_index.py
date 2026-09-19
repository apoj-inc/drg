# SPDX-License-Identifier: Apache-2.0
"""Build reverse indexes for Ansible role dependencies."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


def _role_references(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, list):
        for item in value:
            references.update(_role_references(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            module_name = key.rsplit(".", 1)[-1]
            if module_name in {"include_role", "import_role"}:
                if isinstance(item, str):
                    references.add(item)
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    references.add(item["name"])
            elif key == "roles" and isinstance(item, list):
                for role in item:
                    if isinstance(role, str):
                        references.add(role)
                    elif isinstance(role, dict) and isinstance(role.get("role"), str):
                        references.add(role["role"])
            references.update(_role_references(item))
    return references


@lru_cache(maxsize=None)
def role_names_from_playbook(playbook: Path) -> frozenset[str]:
    try:
        document = yaml.safe_load(playbook.read_text(encoding="utf-8")) or []
    except (OSError, UnicodeError, yaml.YAMLError):
        return frozenset()

    if not isinstance(document, list):
        return frozenset()

    return frozenset(_role_references(document))


@lru_cache(maxsize=None)
def role_dependencies(role_name: str, repo_root: Path) -> frozenset[str]:
    role_dir = repo_root / "shared" / "ansible" / "roles" / role_name
    if not role_dir.is_dir():
        return frozenset()

    result: set[str] = set()
    try:
        role_meta = role_dir / "meta" / "main.yml"
        if role_meta.is_file():
            metadata = yaml.safe_load(role_meta.read_text(encoding="utf-8")) or {}
            dependencies = metadata.get("dependencies", []) if isinstance(metadata, dict) else []
            for dependency in dependencies or []:
                if isinstance(dependency, str):
                    result.add(dependency)
                elif isinstance(dependency, dict) and isinstance(dependency.get("role"), str):
                    result.add(dependency["role"])

        for task_file in (*role_dir.rglob("*.yml"), *role_dir.rglob("*.yaml")):
            document = yaml.safe_load(task_file.read_text(encoding="utf-8")) or []
            result.update(_role_references(document))
    except (OSError, UnicodeError, yaml.YAMLError):
        return frozenset(result)
    return frozenset(result)


@lru_cache(maxsize=None)
def roles_used_by_playbook(playbook: Path, repo_root: Path) -> frozenset[str]:
    roles = set(role_names_from_playbook(playbook))
    pending = list(roles)
    while pending:
        for dependency in role_dependencies(pending.pop(), repo_root) - roles:
            roles.add(dependency)
            pending.append(dependency)
    return frozenset(roles)


def build_role_service_index(
    service_playbooks: dict[str, Path],
    repo_root: Path,
) -> dict[str, set[str]]:
    """Return a reverse index mapping each role to services that use it."""
    index: dict[str, set[str]] = {}
    for service, playbook in service_playbooks.items():
        for role in roles_used_by_playbook(playbook, repo_root):
            index.setdefault(role, set()).add(service)
    return index
