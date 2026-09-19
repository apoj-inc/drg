# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from migrate_terraform_state import (  # noqa: E402
    MigrationError,
    MigrationState,
    Settings,
    build_settings,
    canonical_state_hash,
    migrate,
    resolve_states,
    schema_name,
    slugify_environment,
)


STATE_JSON = json.dumps(
    {"version": 4, "terraform_version": "1.8.0", "serial": 7, "lineage": "test", "resources": []}
)


class FakeTofu:
    def __init__(self, destination_exists: bool = False) -> None:
        self.destination_exists = destination_exists
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> CompletedProcess[str]:
        self.commands.append(command)
        args = command[1:]
        environment = kwargs["env"]
        directory = Path(kwargs["cwd"])
        assert isinstance(environment, dict)
        if args[:1] == ["init"]:
            return CompletedProcess(command, 0, "Terraform initialized", "")
        if args[:2] == ["state", "pull"]:
            if "TF_HTTP_ADDRESS" in environment:
                return CompletedProcess(command, 0, STATE_JSON, "")
            if directory.name == "normalized":
                return CompletedProcess(command, 0, STATE_JSON, "")
            if directory.name == "destination" and (
                self.destination_exists
                or any(
                    "state push" in " ".join(item) and "normalized" not in " ".join(item)
                    for item in self.commands
                )
            ):
                return CompletedProcess(command, 0, STATE_JSON, "")
            return CompletedProcess(command, 1, "No state file was found.", "")
        if args[:2] == ["state", "push"]:
            return CompletedProcess(command, 0, "State written", "")
        return CompletedProcess(command, 1, "unexpected command", "")


class MigrationStateTests(unittest.TestCase):
    def test_forgejo_environment_can_read_legacy_gitlab_settings(self):
        settings = build_settings(
            {
                "GITLAB_API_URL": "https://git.example/api/v4",
                "GITLAB_PROJECT_ID": "42",
                "GITLAB_TERRAFORM_STATE_USERNAME": "state-reader",
                "GITLAB_TERRAFORM_STATE_PASSWORD": "password",
                "PG_CONN_STR": "postgresql://terraform_state@db/terraform_state",
                "PGPASSWORD": "pg-password",
            }
        )
        self.assertEqual(settings.gitlab_api_url, "https://git.example/api/v4")
        self.assertEqual(settings.gitlab_project_id, "42")
        self.assertEqual(settings.gitlab_username, "state-reader")
        self.assertEqual(settings.gitlab_password, "password")

    def test_content_hash_ignores_backend_serial_increment(self) -> None:
        state_with_next_serial = json.dumps(
            {**json.loads(STATE_JSON), "serial": 8}, sort_keys=True
        )
        self.assertNotEqual(canonical_state_hash(STATE_JSON), canonical_state_hash(state_with_next_serial))
        self.assertEqual(
            canonical_state_hash(STATE_JSON, ignore_serial=True),
            canonical_state_hash(state_with_next_serial, ignore_serial=True),
        )

    def test_environment_and_schema_names_match_ci_convention(self) -> None:
        self.assertEqual(slugify_environment("Production / EU"), "production-eu")
        self.assertEqual(schema_name("production-dockerhub"), "terraform_production_dockerhub")

    def test_duplicate_and_schema_collision_are_rejected(self) -> None:
        with self.assertRaisesRegex(MigrationError, "Duplicate"):
            resolve_states(["production-a", "production-a"], [], "production")
        with self.assertRaisesRegex(MigrationError, "same PostgreSQL schema"):
            resolve_states(["production-a-b", "production-a_b"], [], "production")

    def test_explicit_state_names_do_not_require_service_configuration(self) -> None:
        states = resolve_states(["production-dockerhub"], [], "production")
        self.assertEqual(states, [MigrationState("production-dockerhub", "terraform_production_dockerhub")])

    def test_service_selection_expands_rollout_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            (repo_root / "environments" / "example").mkdir(parents=True)
            (repo_root / "services" / "example" / "terraform").mkdir(parents=True)
            (repo_root / "environments" / "example" / "platform.yaml").write_text(
                "rollout_contours:\n  A:\n    order: 10\n  B:\n    order: 20\n",
                encoding="utf-8",
            )
            (repo_root / "services" / "example" / "config.yaml").write_text(
                "rollout:\n  by_contour: true\n"
                "placement:\n  clusters:\n    cluster-a:\n      nodes:\n        server:\n          replicas_by_contour:\n            A: 1\n            B: 0\n",
                encoding="utf-8",
            )
            states = resolve_states([], ["example"], "Production", repo_root)
            self.assertEqual(
                states,
                [MigrationState("production-example-cluster-a-a", "terraform_production_example_cluster_a_a")],
            )

    def test_service_selection_supports_roots_without_service_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            (repo_root / "environments" / "example").mkdir(parents=True)
            (repo_root / "services" / "network" / "terraform").mkdir(parents=True)
            (repo_root / "environments" / "example" / "platform.yaml").write_text(
                "rollout_contours: {}\n",
                encoding="utf-8",
            )
            states = resolve_states([], ["network"], "production", repo_root)
            self.assertEqual(
                states,
                [MigrationState("production-network", "terraform_production_network")],
            )

    def test_dry_run_does_not_push_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTofu()
            settings = Settings(
                repo_root=Path(temporary),
                tofu_bin="tofu",
                mode="plan",
                allow_overwrite=False,
                confirmation="",
                report_path=Path(temporary) / "report.json",
                gitlab_api_url="https://git.example.test/api/v4",
                gitlab_project_id="42",
                gitlab_username="gitlab-ci-token",
                gitlab_password="job-token",
                pg_conn_str="postgres://terraform_state@db/terraform_state",
                pg_password="password",
                pg_schema_prefix="terraform_",
            )
            migrate(settings, [MigrationState("production-dockerhub", "terraform_production_dockerhub")], fake)
            self.assertEqual(
                sum(command[1:3] == ["state", "push"] for command in fake.commands),
                1,
            )
            report = json.loads(settings.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["results"][0]["status"], "dry-run")

    def test_existing_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTofu(destination_exists=True)
            settings = Settings(
                repo_root=Path(temporary),
                tofu_bin="tofu",
                mode="plan",
                allow_overwrite=False,
                confirmation="",
                report_path=Path(temporary) / "report.json",
                gitlab_api_url="https://git.example.test/api/v4",
                gitlab_project_id="42",
                gitlab_username="gitlab-ci-token",
                gitlab_password="job-token",
                pg_conn_str="postgres://terraform_state@db/terraform_state",
                pg_password="password",
                pg_schema_prefix="terraform_",
            )
            with self.assertRaisesRegex(MigrationError, "not empty"):
                migrate(settings, [MigrationState("production-dockerhub", "terraform_production_dockerhub")], fake)

    def test_apply_pushes_and_verifies_state_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTofu()
            settings = Settings(
                repo_root=Path(temporary),
                tofu_bin="tofu",
                mode="apply",
                allow_overwrite=False,
                confirmation="MIGRATE",
                report_path=Path(temporary) / "report.json",
                gitlab_api_url="https://git.example.test/api/v4",
                gitlab_project_id="42",
                gitlab_username="gitlab-ci-token",
                gitlab_password="job-token",
                pg_conn_str="postgres://terraform_state@db/terraform_state",
                pg_password="password",
                pg_schema_prefix="terraform_",
            )
            migrate(settings, [MigrationState("production-dockerhub", "terraform_production_dockerhub")], fake)
            self.assertEqual(
                sum(command[1:3] == ["state", "push"] for command in fake.commands),
                2,
            )
            report = json.loads(settings.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["results"][0]["status"], "migrated")


if __name__ == "__main__":
    unittest.main()
