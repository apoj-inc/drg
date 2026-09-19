#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate Woodpecker plan and deployment workflows from repository topology."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

from generate_pipeline import openstack_platform_phases


REPO_ROOT = Path(os.environ.get("DRG_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
PLATFORM = REPO_ROOT / "environments/example/platform.yaml"
SERVICES = REPO_ROOT / "services"
DEFAULT_OUTPUT = REPO_ROOT / ".woodpecker"


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"ERROR: cannot read {path.relative_to(REPO_ROOT)}: {exc}") from exc
    if not isinstance(document, dict):
        raise SystemExit(f"ERROR: {path.relative_to(REPO_ROOT)} must be a YAML mapping")
    return document


def openstack_enabled() -> bool:
    openstack = read_yaml(PLATFORM).get("openstack", {})
    return isinstance(openstack, dict) and openstack.get("enabled", False)


def rollout_contours() -> list[str]:
    configured = read_yaml(PLATFORM).get("rollout_contours", {})
    if not isinstance(configured, dict) or not configured:
        raise SystemExit("ERROR: platform config must define rollout_contours")
    try:
        ordered = sorted(configured.items(), key=lambda item: item[1]["order"])
    except (KeyError, TypeError) as exc:
        raise SystemExit("ERROR: every rollout contour requires a numeric order") from exc
    contours = [name for name, _ in ordered]
    if not all(isinstance(name, str) and name for name in contours):
        raise SystemExit("ERROR: every rollout contour requires a non-empty name")
    return contours


def rollout_targets(contour: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for service_dir in sorted(path for path in SERVICES.iterdir() if path.is_dir()):
        config_path = service_dir / "config.yaml"
        if not config_path.is_file():
            continue
        config = read_yaml(config_path)
        rollout = config.get("rollout", {})
        if not isinstance(rollout, dict) or rollout.get("by_contour") is not True:
            continue
        placement = config.get("placement", {})
        clusters = placement.get("clusters", {}) if isinstance(placement, dict) else {}
        if not isinstance(clusters, dict):
            raise SystemExit(f"ERROR: {config_path.relative_to(REPO_ROOT)} has invalid placement.clusters")
        for cluster, cluster_config in clusters.items():
            nodes = cluster_config.get("nodes", {}) if isinstance(cluster_config, dict) else {}
            node_configs = nodes.values() if isinstance(nodes, dict) else []
            active = False
            for node_config in node_configs:
                replicas = node_config.get("replicas_by_contour", {}) if isinstance(node_config, dict) else {}
                if isinstance(replicas, dict) and isinstance(replicas.get(contour), int) and replicas[contour] > 0:
                    active = True
            if active:
                result.append((service_dir.name, cluster))
    if not result:
        raise SystemExit(f"ERROR: no rollout targets were found for contour {contour!r}")
    return result


def changed_files() -> list[str] | None:
    raw = os.environ.get("DRG_CHANGED_FILES")
    if raw is None:
        raw = os.environ.get("CI_PIPELINE_FILES", "")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        return None
    normalised: list[str] = []
    for path in value:
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        normalised.append(path)
    return normalised


def pipeline_event() -> str:
    return os.environ.get("CI_PIPELINE_EVENT", "").strip().lower()


def deployment_event() -> bool:
    return pipeline_event() == "deployment"


def deployment_target(contours: list[str]) -> tuple[str, str, str] | None:
    raw = os.environ.get("CI_PIPELINE_DEPLOY_TARGET", "").strip()
    if not raw:
        raw = os.environ.get("FORGEJO_DEPLOY_TARGET", "").strip()
    if not raw:
        if deployment_event():
            raise SystemExit(
                "ERROR: deployment requires CI_PIPELINE_DEPLOY_TARGET "
                "in the form service:cluster:contour"
            )
        return None

    parts = [part.strip() for part in raw.split(":")]
    if len(parts) != 3 or not all(parts):
        raise SystemExit(
            f"ERROR: invalid deployment target {raw!r}; expected service:cluster:contour"
        )
    service, cluster, contour = parts
    normalized_contour = next(
        (configured for configured in contours if configured.lower() == contour.lower()),
        None,
    )
    if normalized_contour is None:
        raise SystemExit(
            f"ERROR: deployment target {raw!r} uses unknown contour {contour!r}"
        )
    return service, cluster, normalized_contour


def deployment_task() -> str:
    task = os.environ.get("CI_PIPELINE_DEPLOY_TASK", "").strip().lower()
    if task and task != "apply":
        raise SystemExit(f"ERROR: unsupported deployment task {task!r}; only 'apply' is allowed")
    return task or "apply"


def deployment_parent_exists() -> bool:
    """Return whether this deployment was started from another pipeline."""
    parent = os.environ.get("CI_PIPELINE_PARENT", "").strip()
    return bool(parent and parent != "0")


def shell_json(value: list[str] | None) -> str:
    return shlex.quote(json.dumps(value or [], separators=(",", ":")))


def workflow_stem(filename: str) -> str:
    return Path(filename).stem


def _dependency_names(value: Any, description: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit(f"ERROR: {description} must be a list")
    result: list[str] = []
    for dependency in value:
        if isinstance(dependency, str):
            name = dependency
        elif isinstance(dependency, dict) and dependency.get("optional") is True:
            name = dependency.get("name")
        elif isinstance(dependency, dict):
            name = dependency.get("name")
        else:
            name = None
        if not isinstance(name, str) or not name:
            raise SystemExit(f"ERROR: {description} contains an invalid dependency")
        result.append(name)
    return result


def validate_workflow_dependencies(workflows: dict[str, str]) -> None:
    """Validate generated workflow and step dependency references."""
    workflow_names = {"validate", *(workflow_stem(name) for name in workflows)}
    for filename, content in workflows.items():
        document = yaml.safe_load(content) or {}
        if not isinstance(document, dict):
            raise SystemExit(f"ERROR: generated workflow {filename} is not a YAML mapping")
        for dependency in _dependency_names(
            document.get("depends_on"), f"{filename}.depends_on"
        ):
            if dependency not in workflow_names:
                raise SystemExit(
                    f"ERROR: generated workflow {filename} depends on unknown workflow {dependency!r}"
                )

        steps = document.get("steps", [])
        if isinstance(steps, dict):
            step_items = steps.items()
        elif isinstance(steps, list):
            step_items = (
                (step.get("name", str(index)), step)
                for index, step in enumerate(steps)
                if isinstance(step, dict)
            )
        else:
            raise SystemExit(f"ERROR: generated workflow {filename}.steps is invalid")
        step_names = {str(name) for name, _ in step_items}
        # The generator emits lists, so materialize once before validating.
        if isinstance(steps, dict):
            step_items = steps.items()
        else:
            step_items = (
                (step.get("name", str(index)), step)
                for index, step in enumerate(steps)
                if isinstance(step, dict)
            )
        for step_name, step in step_items:
            for dependency in _dependency_names(
                step.get("depends_on"), f"{filename}.{step_name}.depends_on"
            ):
                if dependency not in step_names:
                    raise SystemExit(
                        f"ERROR: generated step {filename}.{step_name} depends on unknown step {dependency!r}"
                    )


def pipeline_module() -> Any:
    pipeline_script = REPO_ROOT / "shared/scripts/generate_pipeline.py"
    if not pipeline_script.is_file():
        raise SystemExit(f"ERROR: missing {pipeline_script.relative_to(REPO_ROOT)}")
    pipeline_script_dir = str(pipeline_script.parent)
    if pipeline_script_dir not in sys.path:
        sys.path.insert(0, pipeline_script_dir)
    spec = importlib.util.spec_from_file_location("drg_generate_pipeline", pipeline_script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"ERROR: cannot load {pipeline_script.relative_to(REPO_ROOT)}")
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    return pipeline


def pipeline_jobs(files: list[str] | None) -> list[dict[str, Any]]:
    if files is None:
        return []
    pipeline = pipeline_module()
    return pipeline.build_jobs(pipeline.read_config(), files)


def affected_services(files: list[str] | None, targets: list[tuple[str, str]]) -> set[str]:
    all_services = {service for service, _ in targets}
    if files is None:
        return all_services
    if any(path.startswith("services/freeipa/") for path in files):
        return all_services & {"freeipa", "keycloak", "netbox"}
    jobs = pipeline_jobs(files)
    return {
        job["service"]
        for job in jobs
        if isinstance(job.get("service"), str) and job["service"] in all_services
    }


def render_workflow(
    contour: str,
    previous: str | None,
    targets: list[tuple[str, str]],
    deployment: bool = False,
    files: list[str] | None = None,
    event_filter: str | None = None,
    apply: bool | None = None,
) -> str:
    if apply is None:
        apply = deployment
    dependency = previous or "validate"
    openbao_secret = "openbao_deploy_token" if apply else "openbao_plan_token"
    matrix_lines = "\n".join(
        f"    - SERVICE: {service}\n      CLUSTER: {cluster}"
        for service, cluster in targets
    )
    target = f'"${{SERVICE}}:${{CLUSTER}}:{contour.lower()}"'
    event_filter = event_filter or (
        'CI_PIPELINE_EVENT == "deployment" && '
        '(CI_COMMIT_BRANCH == "drunk" || CI_PIPELINE_PARENT != "0")'
        if deployment
        else 'CI_PIPELINE_EVENT == "pull_request" || (CI_PIPELINE_EVENT == "push" && CI_COMMIT_BRANCH == "drunk")'
    )
    changed_files_export = f"export FORGEJO_CHANGED_FILES={shell_json(files)}"
    steps = f'''  - name: plan
    image: /bin/bash
    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
      - {changed_files_export}
      - export TARGET_JOB_ID={target}
      - python3 shared/scripts/woodpecker_target.py prepare "$TARGET_JOB_ID"
      - dagger -m ci/dagger call plan --source=. --target="$TARGET_JOB_ID" --openbao-token=env:OPENBAO_TOKEN --commit-sha="$CI_COMMIT_SHA" --repository="$CI_REPO" --pipeline-files="$FORGEJO_CHANGED_FILES" --deploy-environment=production
'''
    if apply:
        steps += f'''
  - name: apply
    image: /bin/bash
    depends_on: [plan]
    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
      - {changed_files_export}
      - export TARGET_JOB_ID={target}
      - dagger -m ci/dagger call apply --source=. --target="$TARGET_JOB_ID" --openbao-token=env:OPENBAO_TOKEN --commit-sha="$CI_COMMIT_SHA" --repository="$CI_REPO" --pipeline-files="$FORGEJO_CHANGED_FILES" --deploy-environment=production
'''
    return f'''# Generated by shared/scripts/generate_woodpecker.py; do not edit manually.
when:
  evaluate: '{event_filter}'

depends_on:
  - {dependency}

matrix:
  include:
{matrix_lines}

labels:
  backend: local
  opentofu: "true"
  contour-{contour.lower()}: "true"

concurrency:
  limit: 1
  group: infrastructure-${{CI_REPO}}-${{SERVICE}}-${{CLUSTER}}

steps:
{steps}'''


def render_post_rollout_workflow(
    previous: str | None,
    sync_jobs: list[dict[str, Any]],
    sync_contour: str | None,
    has_traefik_preview: bool,
    has_traefik_apply: bool,
    dns_job_ids: list[str],
    deployment: bool = False,
    files: list[str] | None = None,
    apply: bool | None = None,
) -> str:
    if apply is None:
        apply = deployment
    workflow_dependency = previous or "validate"
    openbao_secret = "openbao_deploy_token" if apply else "openbao_plan_token"
    steps: list[str] = []
    previous_step: str | None = None
    event_filter = (
        'CI_PIPELINE_EVENT == "deployment" && '
        '(CI_COMMIT_BRANCH == "drunk" || CI_PIPELINE_PARENT != "0")'
        if deployment
        else 'CI_PIPELINE_EVENT == "pull_request" || (CI_PIPELINE_EVENT == "push" && CI_COMMIT_BRANCH == "drunk")'
    )
    changed_files_export = f"export FORGEJO_CHANGED_FILES={shell_json(files)}"

    if apply and sync_jobs:
        commands = [
            changed_files_export,
            "python3 shared/scripts/generate_pipeline.py --format forgejo-manifest > .woodpecker-runtime-manifest.json",
        ]
        for job in sync_jobs:
            target = job["job_id"]
            commands.extend(
                [
                    f'export TARGET_JOB_ID="{target}"',
                    'python3 shared/scripts/run_pipeline.py .woodpecker-runtime-manifest.json --phase apply --job-id "$TARGET_JOB_ID" --confirm-apply --allow-empty',
                ]
            )
        steps.append(
            """  - name: ha-sync
    image: /bin/bash
    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
"""
            + "".join(f"      - {command}\n" for command in commands)
        )
        previous_step = "ha-sync"

    if has_traefik_preview:
        dependency = f"    depends_on: [{previous_step}]\n" if previous_step else ""
        steps.append(
            f"""  - name: traefik-preview
    image: /bin/bash
{dependency}    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
      - {changed_files_export}
      - python3 shared/scripts/generate_pipeline.py --format forgejo-manifest > .woodpecker-runtime-manifest.json
      - python3 shared/scripts/run_pipeline.py .woodpecker-runtime-manifest.json --phase traefik-preview
"""
        )
        previous_step = "traefik-preview"

    if apply and has_traefik_apply:
        dependency = f"    depends_on: [{previous_step}]\n" if previous_step else ""
        steps.append(
            f"""  - name: traefik-apply
    image: /bin/bash
{dependency}    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
      - {changed_files_export}
      - python3 shared/scripts/run_pipeline.py .woodpecker-runtime-manifest.json --phase traefik-apply
"""
        )
        previous_step = "traefik-apply"

    if apply and dns_job_ids:
        dependency = f"    depends_on: [{previous_step}]\n" if previous_step else ""
        commands = [
            changed_files_export,
            "python3 shared/scripts/generate_pipeline.py --format forgejo-manifest > .woodpecker-runtime-manifest.json",
        ]
        for job_id in dns_job_ids:
            commands.extend(
                [
                    f'export TARGET_JOB_ID="{job_id}"',
                    'python3 shared/scripts/run_pipeline.py .woodpecker-runtime-manifest.json --phase dns-ingress --job-id "$TARGET_JOB_ID" --allow-empty',
                ]
            )
        steps.append(
            f"""  - name: dns-ingress
    image: /bin/bash
{dependency}    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
"""
            + "".join(f"      - {command}\n" for command in commands)
        )

    contour_label = f'  contour-{sync_contour.lower()}: "true"\n' if sync_contour else ""
    return f'''# Generated by shared/scripts/generate_woodpecker.py; do not edit manually.
when:
  evaluate: '{event_filter}'

depends_on:
  - {workflow_dependency}

labels:
  backend: local
  opentofu: "true"
{contour_label}
concurrency:
  limit: 1
  group: infrastructure-${{CI_REPO}}-post-rollout

steps:
{''.join(steps)}'''


def render_platform_workflow(deployment: bool, files: list[str] | None = None) -> str:
    """Render the explicit OpenStack platform deployment workflow.

    Woodpecker deployments are explicit deployment events, which is the
    manual gate for the platform changes. Service rollout workflows depend on
    this workflow before they can start.
    """
    phases = openstack_platform_phases(files)
    openbao_secret = "openbao_deploy_token"
    changed_files_export = f"export FORGEJO_CHANGED_FILES={shell_json(files)}"
    commands = [changed_files_export]
    commands.extend(
        f"python3 shared/scripts/run_platform.py --phase {phase}"
        for phase in phases
    )
    return f'''# Generated by shared/scripts/generate_woodpecker.py; do not edit manually.
when:
  evaluate: 'CI_PIPELINE_EVENT == "deployment" && (CI_COMMIT_BRANCH == "drunk" || CI_PIPELINE_PARENT != "0")'

depends_on:
  - validate

labels:
  backend: local
  opentofu: "true"
  openstack-platform: "true"

concurrency:
  limit: 1
  group: infrastructure-${{CI_REPO}}-openstack-platform

steps:
  - name: openstack-platform
    image: /bin/bash
    environment:
      OPENBAO_TOKEN:
        from_secret: {openbao_secret}
    commands:
{''.join(f"      - {command}\n" for command in commands)}'''


def expected_workflows() -> dict[str, str]:
    contours = rollout_contours()
    all_targets = [target for contour in contours for target in rollout_targets(contour)]
    files = changed_files()
    deployment = deployment_event()
    # Pushes remain plan-only; deployment is performed by an explicit deployment event.
    automatic_apply = pipeline_event() == "pull_request"
    target = deployment_target(contours)
    if deployment:
        deployment_task()
    if deployment:
        branch = os.environ.get("CI_COMMIT_BRANCH", "").strip()
        if branch != "drunk" and not deployment_parent_exists():
            raise SystemExit(
                "ERROR: deployment is allowed only from the protected drunk branch "
                "or from a successful parent pipeline"
            )
    selected_services = affected_services(files, all_targets)
    if deployment and target is not None:
        target_service, target_cluster, target_contour = target
        if target_service not in selected_services:
            raise SystemExit(
                f"ERROR: deployment target {target_service}:{target_cluster}:{target_contour.lower()} "
                "is not affected by this commit"
            )
        if (target_service, target_cluster) not in rollout_targets(target_contour):
            raise SystemExit(
                f"ERROR: deployment target {target_service}:{target_cluster}:{target_contour.lower()} "
                "is not a configured rollout target"
            )
    else:
        target_service = target_cluster = target_contour = None
    jobs = pipeline_jobs(files)
    sync_jobs = [
        job
        for job in jobs
        if job.get("template") == "ha_sync.yml.j2"
        and (
            not deployment
            or (
                job.get("service") == target_service
                and job.get("deployment_cluster") == target_cluster
                and job.get("deployment_contour") == target_contour
            )
        )
    ]
    sync_contours = [job.get("deployment_contour") for job in sync_jobs if job.get("deployment_contour")]
    sync_contour = sync_contours[0] if sync_contours else None
    dns_job_ids = sorted(
        job["job_id"]
        for job in jobs
        if job.get("template") == "dns-reconcile.yml.j2"
        and isinstance(job.get("job_id"), str)
        and job.get("dns_stage") == "dns_ingress"
        and (
            not deployment
            or (
                job.get("service") == target_service
                and job.get("deployment_cluster") == target_cluster
                and job.get("deployment_contour") == target_contour
            )
        )
    )
    has_traefik_preview = any(job.get("template") == "traefik-preview.yml.j2" for job in jobs)
    has_traefik_apply = any(job.get("template") == "traefik-apply.yml.j2" for job in jobs)

    workflows: dict[str, str] = {}
    previous: str | None = None
    if deployment and openstack_enabled() and openstack_platform_phases(files):
        platform_filename = "openstack-platform-deploy.yml"
        workflows[platform_filename] = render_platform_workflow(True, files)
        previous = platform_filename.removesuffix(".yml")
    first_plan_contour: str | None = None
    if not deployment:
        first_plan_contour = next(
            (
                contour
                for contour in contours
                if any(
                    service in selected_services
                    for service, _ in rollout_targets(contour)
                )
            ),
            None,
        )

    for contour in contours:
        if not deployment and contour != first_plan_contour:
            continue
        targets = [target_item for target_item in rollout_targets(contour) if target_item[0] in selected_services]
        if deployment and target is not None:
            targets = [
                target_item
                for target_item in targets
                if target_item == (target_service, target_cluster)
                and contour == target_contour
            ]
        if not targets:
            continue
        filename = (
            f"rollout-deploy-{contour.lower()}.yml"
            if deployment
            else f"rollout-{contour.lower()}.yml"
        )
        workflows[filename] = render_workflow(
            contour,
            previous,
            targets,
            deployment,
            files,
            apply=deployment or automatic_apply,
        )
        previous = filename.removesuffix(".yml")

    has_post_work = (
        ((deployment or automatic_apply) and (sync_jobs or has_traefik_preview or has_traefik_apply or dns_job_ids))
        or (not deployment and not automatic_apply and has_traefik_preview)
    )
    if has_post_work:
        filename = (
            "post-rollout-deploy.yml"
            if deployment
            else "post-rollout.yml"
            if automatic_apply
            else "post-rollout-plan.yml"
        )
        workflows[filename] = render_post_rollout_workflow(
            previous,
            sync_jobs,
            sync_contour,
            has_traefik_preview,
            has_traefik_apply,
            dns_job_ids,
            deployment,
            files,
            apply=deployment or automatic_apply,
        )
        previous = filename.removesuffix(".yml")

    validate_workflow_dependencies(workflows)
    return workflows


def check(output_dir: Path, expected: dict[str, str]) -> int:
    errors = 0
    for filename, content in expected.items():
        path = output_dir / filename
        if not path.is_file():
            print(f"missing generated workflow: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            errors += 1
        elif path.read_text(encoding="utf-8") != content:
            print(f"outdated generated workflow: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            errors += 1
    stale_paths = list(output_dir.glob("rollout-*.yml")) if output_dir.is_dir() else []
    if output_dir.is_dir():
        stale_paths.extend(output_dir.glob("ha-sync.yml"))
        stale_paths.extend(output_dir.glob("post-rollout*.yml"))
    for path in stale_paths:
        if path.name not in expected:
            print(f"stale generated workflow: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.output_dir.is_absolute():
        args.output_dir = REPO_ROOT / args.output_dir
    expected = expected_workflows()
    if args.check:
        return check(args.output_dir, expected)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in expected.items():
        (args.output_dir / filename).write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
