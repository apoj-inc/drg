#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the provider-neutral Forgejo deployment manifest.

The GitLab templates remain the compatibility implementation.  Forgejo cannot
expand a workflow from YAML emitted by an earlier job, so this runner consumes
the generated manifest and keeps rollout ordering in one explicit process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn




def find_repo_root() -> Path:
    """Find the checkout containing this shared pipeline script.

    The script is normally located at ``<checkout>/shared/scripts``.  Walking
    its resolved parents keeps that layout working for both GitLab and Forgejo
    checkouts, even when the current working directory is different.
    """
    script = Path(__file__).resolve()
    for candidate in script.parents:
        if (
            (candidate / "shared" / "scripts" / "run_pipeline.py").is_file()
            and (candidate / "services").is_dir()
        ):
            return candidate
    raise RuntimeError(f"Cannot locate repository root from {script}")


REPO_ROOT = find_repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
ANSIBLE_COLLECTIONS_ROOT = REPO_ROOT / ".ansible" / "collections"
ANSIBLE_COLLECTIONS_REQUIREMENTS = (
    REPO_ROOT / "shared/ansible/collections/requirements.yml"
)
ANSIBLE_COLLECTIONS_MARKER = REPO_ROOT / ".ansible" / ".requirements.sha256"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"ERROR: {message}")


def run(command: list[str], env: dict[str, str], *, cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(shlex.quote(item) for item in command))
    result = subprocess.run(command, cwd=cwd, env=env, check=False)
    if result.returncode:
        fail(f"command failed with exit code {result.returncode}: {command[0]}")


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read manifest {path}: {exc}")
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        fail("Forgejo manifest must be an object with version 1")
    if not isinstance(manifest.get("jobs"), list):
        fail("Forgejo manifest jobs must be a list")
    return manifest


def load_env_file(path: Path, env: dict[str, str]) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"cannot read generated OpenBao environment: {exc}")
    for line in lines:
        if not line.startswith("export "):
            continue
        name, value = line[7:].split("=", 1)
        env[name] = shlex.split(value)[0] if value else ""


def load_openbao(job: dict[str, Any], job_type: str, env: dict[str, str]) -> None:
    config = job.get("openbao")
    if not config:
        return
    if not isinstance(config, dict):
        fail("job OpenBao configuration must be an object")
    with tempfile.NamedTemporaryFile(prefix="openbao-env-", delete=False) as output:
        output_path = Path(output.name)
    try:
        loader_env = dict(env)
        loader_env.update(
            {
                "OPENBAO_SECRET_JOB": job_type,
                "OPENBAO_SECRET_SPECS": json.dumps(config.get("secrets", [])),
                "VAULT_SERVER_URL": str(config["server_url"]),
                "VAULT_AUTH_PATH": str(config.get("auth_path", config.get("forgejo_auth_path", "forgejo-actions"))),
                "VAULT_AUTH_ROLE": str(config.get("auth_role", config.get("forgejo_auth_role", "ci"))),
                "OPENBAO_ID_TOKEN_NAME": str(config.get("id_token_name", "OPENBAO_ID_TOKEN")),
                "OPENBAO_OIDC_AUDIENCE": str(config.get("audience", "https://openbao.example.test")),
            }
        )
        run([sys.executable, str(SCRIPT_DIR / "load_openbao_secrets.py"), str(output_path)], loader_env)
        load_env_file(output_path, env)
    finally:
        output_path.unlink(missing_ok=True)


def ensure_ansible_collections(env: dict[str, str]) -> None:
    """Install repository-declared collections and expose them to Ansible."""
    ANSIBLE_COLLECTIONS_ROOT.mkdir(parents=True, exist_ok=True)
    configured_paths = env.get("ANSIBLE_COLLECTIONS_PATH", "")
    collection_paths = [str(ANSIBLE_COLLECTIONS_ROOT)]
    collection_paths.extend(
        path for path in configured_paths.split(os.pathsep) if path
    )
    collection_paths.extend(
        path for path in ("/usr/share/ansible/collections",) if path not in collection_paths
    )
    env["ANSIBLE_COLLECTIONS_PATH"] = os.pathsep.join(dict.fromkeys(collection_paths))
    requirements_digest = hashlib.sha256(
        ANSIBLE_COLLECTIONS_REQUIREMENTS.read_bytes()
    ).hexdigest()
    if (
        ANSIBLE_COLLECTIONS_MARKER.is_file()
        and ANSIBLE_COLLECTIONS_MARKER.read_text(encoding="utf-8").strip()
        == requirements_digest
    ):
        return
    run(
        [
            "ansible-galaxy",
            "collection",
            "install",
            "-r",
            str(ANSIBLE_COLLECTIONS_REQUIREMENTS),
            "-p",
            str(ANSIBLE_COLLECTIONS_ROOT),
        ],
        env,
    )
    ANSIBLE_COLLECTIONS_MARKER.parent.mkdir(parents=True, exist_ok=True)
    ANSIBLE_COLLECTIONS_MARKER.write_text(f"{requirements_digest}\n", encoding="utf-8")


def prepare_env(job: dict[str, Any], job_type: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("FORGEJO_WORKSPACE", str(REPO_ROOT))
    env.setdefault("NETBOX_API", str(job.get("netbox_api", "")))
    env["SERVICE"] = str(job.get("service", ""))
    env["DEPLOY_ENVIRONMENT"] = str(job.get("deploy_environment", env.get("ENVIRONMENT", "production")))
    env["ENVIRONMENT"] = env["DEPLOY_ENVIRONMENT"]
    env["TF_STATE_NAME"] = str(job.get("state_name") or job.get("service", ""))
    if job.get("deployment_cluster"):
        env["DEPLOYMENT_CLUSTER"] = str(job["deployment_cluster"])
        env["DEPLOYMENT_CONTOUR"] = str(job["deployment_contour"])
    load_openbao(job, job_type, env)
    return env


def terraform_env(job: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    result = dict(env)
    state_name = result["TF_STATE_NAME"]
    result["PG_CONN_STR"] = "postgresql://terraform_state@postgresql.example.test:5432/terraform_state?sslmode=disable"
    result["PG_SCHEMA_NAME"] = "terraform_" + "".join(
        char if char.isalnum() or char == "_" else "_" for char in state_name
    )
    result.setdefault("TF_INPUT", "false")
    result.setdefault("TOFU_IN_AUTOMATION", "true")
    key_file = result.get("ANSIBLE_PRIVATE_KEY_FILE")
    public_keys = result.get("TF_VAR_ssh_public_keys")
    if key_file and Path(key_file).is_file() and public_keys:
        result["IPA_BOOTSTRAP_PRIVATE_KEY"] = key_file
        key_result = subprocess.run(
            ["ssh-keygen", "-y", "-f", key_file], capture_output=True, text=True, check=False
        )
        if key_result.returncode == 0:
            try:
                keys = json.loads(public_keys)
                if isinstance(keys, list) and all(isinstance(key, str) for key in keys):
                    bootstrap_key = key_result.stdout.strip()
                    result["TF_VAR_ssh_public_keys"] = json.dumps(
                        [key for key in keys if key.strip() != bootstrap_key] + [bootstrap_key],
                        separators=(",", ":"),
                    )
            except json.JSONDecodeError:
                pass
    return result


def terraform_root(job: dict[str, Any]) -> Path:
    root = REPO_ROOT / "services" / str(job["service"]) / "terraform"
    if not root.is_dir():
        fail(f"Terraform root does not exist: {root}")
    return root


def tofu_plan(job: dict[str, Any], env: dict[str, str]) -> None:
    root = terraform_root(job)
    tofu_env = terraform_env(job, env)
    run(["tofu", "init", "-reconfigure"], tofu_env, cwd=root)
    run(["tofu", "plan", "-out=tfplan"], tofu_env, cwd=root)
    run(["tofu", "show", "-no-color", "tfplan"], tofu_env, cwd=root)


def tofu_apply(job: dict[str, Any], env: dict[str, str]) -> None:
    root = terraform_root(job)
    run(["tofu", "apply", "-auto-approve", "tfplan"], terraform_env(job, env), cwd=root)


def ansible_configure(job: dict[str, Any], env: dict[str, str]) -> None:
    key_file = env.get("ANSIBLE_PRIVATE_KEY_FILE", "")
    if not key_file or not Path(key_file).is_file():
        fail("ANSIBLE_PRIVATE_KEY_FILE is not available from OpenBao")
    ansible_env = dict(env)
    ansible_env["ANSIBLE_USER"] = "ansible-ci"
    ansible_env["ANSIBLE_PRIVATE_KEY_FILE"] = ""
    ansible_env["ANSIBLE_AUTH_MODE"] = "password"
    ansible_env["ANSIBLE_SSH_COMMON_ARGS"] = (
        "-o StrictHostKeyChecking=accept-new -o GSSAPIAuthentication=no "
        "-o PreferredAuthentications=password,keyboard-interactive "
        "-o PubkeyAuthentication=no -o PasswordAuthentication=yes"
    )
    ansible_env["ANSIBLE_LIMIT"] = str(job["ansible_limit"])
    ansible_env["NETBOX_VALIDATE_CERTS"] = ansible_env.get("NETBOX_VALIDATE_CERTS", "true")
    ensure_ansible_collections(ansible_env)
    playbook = REPO_ROOT / str(job["playbook"])
    run(
        ["ansible-playbook", "-i", "shared/ansible/inventory/netbox.yml", str(playbook), "--syntax-check", "--limit", ansible_env["ANSIBLE_LIMIT"]],
        ansible_env,
    )
    run(
        ["ansible-playbook", "-i", "shared/ansible/inventory/netbox.yml", str(playbook), "--limit", ansible_env["ANSIBLE_LIMIT"]],
        ansible_env,
    )


def jobs_for(manifest: dict[str, Any], template: str) -> list[dict[str, Any]]:
    return [job for job in manifest["jobs"] if job.get("template") == template]


def ansible_jobs_for(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return jobs_for(manifest, "configure.yml.j2") + jobs_for(manifest, "ha_sync.yml.j2")


def execute_ansible_phase(
    manifest: dict[str, Any],
    template: str,
    job_id: str | None,
    allow_empty: bool,
) -> None:
    for job in selected_jobs(manifest, template, job_id, allow_empty):
        print(f"== configure {job.get('job_id', template)} ==")
        ansible_configure(job, prepare_env(job, "ansible"))


def selected_jobs(
    manifest: dict[str, Any], template: str, job_id: str | None, allow_empty: bool = False
) -> list[dict[str, Any]]:
    jobs = jobs_for(manifest, template)
    if not job_id:
        return jobs
    selected = [job for job in jobs if job.get("job_id") == job_id]
    if not selected and not allow_empty:
        fail(f"job {job_id!r} does not have a {template} entry in the manifest")
    return selected


def validate(manifest: dict[str, Any]) -> None:
    ansible_jobs = ansible_jobs_for(manifest)
    ansible_env = dict(os.environ)
    if ansible_jobs:
        ensure_ansible_collections(ansible_env)
    for job in jobs_for(manifest, "validate.yml.j2"):
        if job.get("terraform_needed"):
            root = terraform_root(job)
            run(["tofu", "init", "-backend=false"], dict(os.environ), cwd=root)
            run(["tofu", "validate"], dict(os.environ), cwd=root)
        if job.get("configure_needed"):
            run(
                ["ansible-playbook", "-i", "localhost,", str(REPO_ROOT / job["playbook"]), "--syntax-check"],
                ansible_env,
            )


def execute(
    manifest: dict[str, Any],
    phase: str,
    confirm_apply: bool,
    job_id: str | None,
    allow_empty: bool,
) -> None:
    if phase == "validate":
        validate(manifest)
        return

    plan_jobs = selected_jobs(manifest, "plan.yml.j2", job_id, allow_empty)
    configure_jobs = selected_jobs(manifest, "configure.yml.j2", job_id, allow_empty)
    ha_sync_jobs = selected_jobs(manifest, "ha_sync.yml.j2", job_id, allow_empty)
    ansible_configure_jobs = configure_jobs + ha_sync_jobs
    if phase == "plan":
        if not plan_jobs:
            print("No Terraform plans are required.")
        for job in plan_jobs:
            print(f"== plan {job['job_id']} ==")
            tofu_plan(job, prepare_env(job, "terraform"))
        return
    if phase == "apply":
        if not job_id:
            fail("apply requires --job-id")
        if not confirm_apply:
            fail("apply requires confirm_apply=true")
        apply_jobs = selected_jobs(manifest, "apply.yml.j2", job_id, allow_empty)
        if apply_jobs:
            tofu_apply(apply_jobs[0], prepare_env(apply_jobs[0], "terraform"))
        for job in ansible_configure_jobs:
            print(f"== configure {job['job_id']} ==")
            ansible_configure(job, prepare_env(job, "ansible"))
        return
    if phase == "traefik-preview":
        execute_ansible_phase(manifest, "traefik-preview.yml.j2", job_id, allow_empty)
        return
    if phase == "traefik-apply":
        execute_ansible_phase(manifest, "traefik-apply.yml.j2", job_id, allow_empty)
        return
    if phase == "dns-ingress":
        execute_ansible_phase(manifest, "dns-reconcile.yml.j2", job_id, allow_empty)
        return
    if phase != "deploy":
        fail(f"unsupported phase: {phase}")
    if not confirm_apply:
        fail("deployment requires confirm_apply=true")

    if not plan_jobs:
        print("No Terraform plans are required.")
    for job in plan_jobs:
        print(f"== plan {job['job_id']} ==")
        tofu_plan(job, prepare_env(job, "terraform"))

    apply_by_id = {job["job_id"]: job for job in jobs_for(manifest, "apply.yml.j2")}
    configure_by_id = {job["job_id"]: job for job in ansible_configure_jobs}
    ordered_ids = list(dict.fromkeys(
        [job["job_id"] for job in plan_jobs] + [job["job_id"] for job in ansible_configure_jobs]
    ))
    plan_by_id = {job["job_id"]: job for job in plan_jobs}
    for job_id in ordered_ids:
        job = plan_by_id.get(job_id)
        if job_id in apply_by_id:
            print(f"== apply {job_id} ==")
            tofu_apply(apply_by_id[job_id], prepare_env(apply_by_id[job_id], "terraform"))
        if job_id in configure_by_id:
            print(f"== configure {job_id} ==")
            configure_job = configure_by_id[job_id]
            ansible_configure(configure_job, prepare_env(configure_job, "ansible"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--phase",
        choices=("validate", "plan", "apply", "traefik-preview", "traefik-apply", "dns-ingress", "deploy"),
        required=True,
    )
    parser.add_argument("--confirm-apply", action="store_true")
    parser.add_argument("--job-id", help="Limit plan/apply to one generated service or rollout target job")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Treat a target missing from an affected-services manifest as a no-op",
    )
    args = parser.parse_args()
    execute(read_manifest(args.manifest), args.phase, args.confirm_apply, args.job_id, args.allow_empty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
