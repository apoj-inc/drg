#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the manual Terraform state migration command pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:
    missing = exc.name or "required dependency"
    print(f"ERROR: Missing Python dependency {missing!r}. Install Jinja2.", file=sys.stderr)
    raise SystemExit(1) from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "shared" / "templates" / "terraform_state_migration"


def render_pipeline() -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    jobs: list[dict[str, Any]] = [
        {"template": "state_names.yml.j2"},
        {"template": "services.yml.j2"},
    ]
    return environment.get_template("wrapper.yml.j2").render(jobs=jobs)


def main() -> int:
    print(render_pipeline(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
