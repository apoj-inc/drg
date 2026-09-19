# SPDX-License-Identifier: Apache-2.0
"""Dagger execution module for the infrastructure pipeline."""

from __future__ import annotations

import shlex
from typing import Annotated

import dagger
from dagger import Doc, dag, function, object_type


OPENTOFU_VERSION = "1.12.3"


@object_type
class DrgCi:
    """Run repository validation and deployment phases in a Dagger container."""

    def _toolchain(
        self,
        source: dagger.Directory,
        *,
        openbao_token: dagger.Secret | None = None,
        commit_sha: str = "",
        repository: str = "example-org/drg",
        pipeline_files: str = "",
        deploy_environment: str = "production",
        target: str = "",
    ) -> dagger.Container:
        container = (
            dag.container()
            .from_("debian:13-slim")
            .with_exec(
                [
                    "sh",
                    "-c",
                    "apt-get update && apt-get install -y --no-install-recommends "
                    "ansible ca-certificates curl git openssh-client python3 "
                    "python3-jinja2 python3-pip python3-pynetbox python3-requests "
                    "python3-requests-kerberos python3-tz python3-yaml yamllint "
                    "&& rm -rf /var/lib/apt/lists/*",
                ]
            )
            .with_exec(
                [
                    "sh",
                    "-c",
                    "curl -fsSL -o /tmp/tofu.deb "
                    f"https://github.com/opentofu/opentofu/releases/download/v{OPENTOFU_VERSION}/"
                    f"tofu_{OPENTOFU_VERSION}_amd64.deb && "
                    "apt-get install -y /tmp/tofu.deb && rm -f /tmp/tofu.deb",
                ]
            )
            .with_new_file(
                "/root/.tofurc",
                "provider_installation {\n"
                "  network_mirror {\n"
                "    url = \"https://terraform-mirror.yandexcloud.net/\"\n"
                "    include = [\"registry.opentofu.org/*/*\"]\n"
                "  }\n"
                "  direct {\n"
                "    exclude = [\"registry.opentofu.org/*/*\"]\n"
                "  }\n"
                "}\n",
            )
            .with_env_variable("TF_CLI_CONFIG_FILE", "/root/.tofurc")
            .with_directory("/workspace", source)
            .with_workdir("/workspace")
            .with_env_variable("CI", "woodpecker")
            .with_env_variable("CI_COMMIT_SHA", commit_sha)
            .with_env_variable("FORGEJO_SHA", commit_sha)
            .with_env_variable("CI_REPO", repository)
            .with_env_variable("FORGEJO_REPOSITORY", repository)
            .with_env_variable("CI_PIPELINE_FILES", pipeline_files)
            # generate_woodpecker.py uses this explicit input for affected-service
            # filtering. Keep it aligned with Woodpecker's changed-files metadata.
            .with_env_variable("DRG_CHANGED_FILES", pipeline_files)
            .with_env_variable("DEPLOY_ENVIRONMENT", deploy_environment)
            .with_env_variable("TF_PLUGIN_CACHE_DIR", "/root/.terraform.d/plugin-cache")
            .with_mounted_cache(
                "/root/.terraform.d/plugin-cache",
                dag.cache_volume("drg-tofu-provider-cache"),
            )
        )
        if target:
            target_name = "-".join(
                part for part in target.replace("/", "-").split(":") if part
            )
            commit_name = "".join(
                char if char.isalnum() or char in "-_" else "-"
                for char in commit_sha
            ) or "worktree"
            cache_name = f"drg-tfplan-{target_name}-{commit_name}"
            container = container.with_mounted_cache(
                "/dagger-tfplan", dag.cache_volume(cache_name)
            )
        if openbao_token is not None:
            container = container.with_secret_variable("OPENBAO_TOKEN", openbao_token)
        return container

    async def _run(
        self,
        source: dagger.Directory,
        command: list[str],
        *,
        openbao_token: dagger.Secret | None = None,
        commit_sha: str = "",
        repository: str = "example-org/drg",
        pipeline_files: str = "",
        deploy_environment: str = "production",
        target: str = "",
    ) -> str:
        return await self._toolchain(
            source,
            openbao_token=openbao_token,
            commit_sha=commit_sha,
            repository=repository,
            pipeline_files=pipeline_files,
            deploy_environment=deploy_environment,
            target=target,
        ).with_exec(command).stdout()

    @function
    async def validate(
        self,
        source: Annotated[dagger.Directory, Doc("Repository checkout")],
        commit_sha: str = "",
        repository: str = "example-org/drg",
        pipeline_files: str = "",
        deploy_environment: str = "production",
    ) -> str:
        """Validate the repository and generated deployment manifest."""
        return await self._run(
            source,
            [
                "sh",
                "-c",
                "python3 shared/scripts/validate_repository.py && "
                "generated_dir=$(mktemp -d) && "
                "python3 shared/scripts/generate_woodpecker.py --output-dir \"$generated_dir\" && "
                "python3 shared/scripts/generate_pipeline.py --format forgejo-manifest > forgejo-manifest.json && "
                "python3 -m json.tool forgejo-manifest.json > /dev/null && "
                "python3 shared/scripts/run_pipeline.py forgejo-manifest.json --phase validate",
            ],
            commit_sha=commit_sha,
            repository=repository,
            pipeline_files=pipeline_files,
            deploy_environment=deploy_environment,
        )

    @function
    async def plan(
        self,
        source: Annotated[dagger.Directory, Doc("Repository checkout")],
        target: Annotated[str, Doc("Generated service/cluster/contour job id")],
        openbao_token: Annotated[dagger.Secret, Doc("OpenBao CI token")],
        commit_sha: str = "",
        repository: str = "example-org/drg",
        pipeline_files: str = "",
        deploy_environment: str = "production",
    ) -> str:
        """Generate and execute one affected target's OpenTofu plan."""
        return await self._run(
            source,
            [
                "sh",
                "-c",
                "python3 shared/scripts/run_pipeline.py "
                ".woodpecker-runtime-manifest.json --phase plan --job-id "
                + shlex.quote(target)
                + " --allow-empty && "
                "service="
                + shlex.quote(target.split(":", 1)[0])
                + " && if test -f \"services/$service/terraform/tfplan\"; then "
                "cp \"services/$service/terraform/tfplan\" /dagger-tfplan/tfplan; fi",
            ],
            openbao_token=openbao_token,
            commit_sha=commit_sha,
            repository=repository,
            pipeline_files=pipeline_files,
            deploy_environment=deploy_environment,
            target=target,
        )

    @function
    async def apply(
        self,
        source: Annotated[dagger.Directory, Doc("Repository checkout")],
        target: Annotated[str, Doc("Generated service/cluster/contour job id")],
        openbao_token: Annotated[dagger.Secret, Doc("OpenBao CI token")],
        commit_sha: str = "",
        repository: str = "example-org/drg",
        pipeline_files: str = "",
        deploy_environment: str = "production",
    ) -> str:
        """Apply one previously planned target."""
        return await self._run(
            source,
            [
                "sh",
                "-c",
                "service="
                + shlex.quote(target.split(":", 1)[0])
                + " && if test -f /dagger-tfplan/tfplan; then "
                "cp /dagger-tfplan/tfplan \"services/$service/terraform/tfplan\"; fi && "
                "python3 shared/scripts/run_pipeline.py "
                ".woodpecker-runtime-manifest.json --phase apply --job-id "
                + shlex.quote(target)
                + " --confirm-apply --allow-empty",
            ],
            openbao_token=openbao_token,
            commit_sha=commit_sha,
            repository=repository,
            pipeline_files=pipeline_files,
            deploy_environment=deploy_environment,
            target=target,
        )
