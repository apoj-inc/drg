#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

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
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / 'shared' / 'templates' / 'docker_utils'


def build_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    manual_util = {
        'stage': 'manual',
        'when': 'manual',
    }
    for util in Path(TEMPLATE_DIR).iterdir():
        if util.name != 'wrapper.yml.j2':
            jobs.append({**manual_util, "template": util.name})
    return jobs


def render_pipeline(jobs: list[dict[str, Any]]) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = environment.get_template('wrapper.yml.j2')
    return template.render(
        include_template=None,
        stages=['manual'],
        jobs=jobs,
    )


def main() -> int:
    jobs = build_jobs()
    print(render_pipeline(jobs), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
