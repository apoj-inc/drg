#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run one affected infrastructure target from a Woodpecker workflow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / ".woodpecker-runtime-manifest.json"
ACTIVE = ROOT / ".woodpecker-runtime-active"


def environment() -> dict[str, str]:
    env = dict(os.environ)
    env["FORGEJO_SHA"] = env.get("CI_COMMIT_SHA", "")
    env["FORGEJO_REPOSITORY"] = env.get("CI_REPO", "example-org/drg")
    env["DEPLOY_ENVIRONMENT"] = env.get("DEPLOY_ENVIRONMENT", "production")
    return env


def generate_manifest(env: dict[str, str]) -> dict:
    generator = ROOT / "shared/scripts/generate_pipeline.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--format", "forgejo-manifest"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: generated manifest is invalid: {exc}") from exc
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def target_is_affected(manifest: dict, job_id: str) -> bool:
    return any(
        job.get("job_id") == job_id
        and job.get("template") in {
            "plan.yml.j2",
            "apply.yml.j2",
            "configure.yml.j2",
            "ha_sync.yml.j2",
        }
        for job in manifest.get("jobs", [])
    )


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "prepare":
        raise SystemExit("Usage: woodpecker_target.py prepare <job-id>")
    phase, job_id = sys.argv[1:]
    ACTIVE.unlink(missing_ok=True)
    manifest = generate_manifest(environment())
    if not target_is_affected(manifest, job_id):
        print(f"No affected jobs for {job_id}; skipping target.")
        return 0
    ACTIVE.write_text(job_id + "\n", encoding="utf-8")
    print(f"Affected target: {job_id}; Dagger will execute the plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
