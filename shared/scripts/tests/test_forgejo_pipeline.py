# SPDX-License-Identifier: Apache-2.0
import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import generate_pipeline  # noqa: E402
import generate_woodpecker  # noqa: E402
import pipeline_graph  # noqa: E402
import run_pipeline  # noqa: E402


class ForgejoPipelineTests(unittest.TestCase):
    def test_woodpecker_server_uses_etcd_active_passive_candidates(self):
        config = generate_woodpecker.read_yaml(
            generate_woodpecker.REPO_ROOT / "services/woodpecker/config.yaml"
        )
        replicas = config["placement"]["clusters"]["example-cluster"]["nodes"]["pve-node-a"][
            "replicas_by_contour"
        ]

        self.assertEqual(config["service"]["topology"], "etcd-active-passive")
        self.assertEqual(replicas, {"A": 1, "B": 1})

    def test_woodpecker_rollout_selection_matches_pipeline_dependencies(self):
        targets = [
            ("freeipa", "example-cluster"),
            ("keycloak", "example-cluster"),
            ("netbox", "example-cluster"),
        ]

        self.assertEqual(
            generate_woodpecker.affected_services(["services/keycloak/config.yaml"], targets),
            {"keycloak"},
        )
        self.assertEqual(
            generate_woodpecker.affected_services(["ci/dagger/src/drg_ci/__init__.py"], targets),
            set(),
        )
        self.assertEqual(
            generate_woodpecker.affected_services(["environments/example/platform.yaml"], targets),
            {"freeipa", "keycloak", "netbox"},
        )
        self.assertEqual(
            generate_woodpecker.affected_services(None, targets),
            {"freeipa", "keycloak", "netbox"},
        )

    def test_changed_files_override_is_provider_neutral(self):
        with patch.dict(os.environ, {"FORGEJO_CHANGED_FILES": "services/foo/config.yaml shared/x.tf"}, clear=False):
            self.assertEqual(
                generate_pipeline.changed_files_from_environment(),
                ["services/foo/config.yaml", "shared/x.tf"],
            )

    def test_json_changed_files_override_is_parsed_for_manifest_generation(self):
        changed_files = ["services/forgejo/config.yaml", "shared/scripts/run_pipeline.py"]
        with patch.dict(
            os.environ,
            {"FORGEJO_CHANGED_FILES": json.dumps(changed_files)},
            clear=True,
        ):
            self.assertEqual(
                generate_pipeline.changed_files_from_environment(),
                changed_files,
            )

    def test_gitlab_merge_request_base_wins_over_forgejo_metadata(self):
        class DiffResult:
            returncode = 0
            stdout = "services/woodpecker/config.yaml\n"

        with patch.dict(
            os.environ,
            {
                "CI_PIPELINE_SOURCE": "merge_request_event",
                "CI_MERGE_REQUEST_IID": "42",
                "CI_MERGE_REQUEST_DIFF_BASE_SHA": "gitlab-base",
                "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": "gitlab-source-head",
                "FORGEJO_BASE_SHA": "stale-forgejo-base",
                "CI_COMMIT_SHA": "gitlab-head",
            },
            clear=True,
        ), patch("generate_pipeline.subprocess.run", return_value=DiffResult()) as run:
            self.assertEqual(
                generate_pipeline.changed_files_from_environment(),
                ["services/woodpecker/config.yaml"],
            )

        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--name-only", "gitlab-base", "gitlab-source-head"],
        )

    def test_gitlab_regular_merge_request_falls_back_to_pipeline_commit(self):
        class DiffResult:
            returncode = 0
            stdout = "services/woodpecker/config.yaml\n"

        with patch.dict(
            os.environ,
            {
                "CI_PIPELINE_SOURCE": "merge_request_event",
                "CI_MERGE_REQUEST_IID": "42",
                "CI_MERGE_REQUEST_DIFF_BASE_SHA": "gitlab-base",
                "CI_COMMIT_SHA": "gitlab-head",
            },
            clear=True,
        ), patch("generate_pipeline.subprocess.run", return_value=DiffResult()) as run:
            self.assertEqual(
                generate_pipeline.changed_files_from_environment(),
                ["services/woodpecker/config.yaml"],
            )

        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--name-only", "gitlab-base", "gitlab-head"],
        )

    def test_gitlab_merged_result_without_source_sha_uses_target_branch_tip(self):
        class DiffResult:
            returncode = 0
            stdout = "services/keycloak/config.yaml\n"

        with patch.dict(
            os.environ,
            {
                "GITLAB_CI": "true",
                "CI_PIPELINE_SOURCE": "merge_request_event",
                "CI_MERGE_REQUEST_IID": "42",
                "CI_MERGE_REQUEST_EVENT_TYPE": "merged_result",
                "CI_MERGE_REQUEST_DIFF_BASE_SHA": "gitlab-base",
                "CI_MERGE_REQUEST_TARGET_BRANCH_SHA": "gitlab-target-head",
                "CI_COMMIT_SHA": "merged-result-head",
            },
            clear=True,
        ), patch("generate_pipeline.subprocess.run", return_value=DiffResult()) as run:
            self.assertEqual(
                generate_pipeline.changed_files_from_environment(),
                ["services/keycloak/config.yaml"],
            )

        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--name-only", "gitlab-target-head", "merged-result-head"],
        )

    def test_gitlab_merged_result_ignores_target_branch_changes_for_service_selection(self):
        class DiffResult:
            returncode = 0
            stdout = "services/keycloak/config.yaml\n"

        with patch.dict(
            os.environ,
            {
                "GITLAB_CI": "true",
                "CI_PIPELINE_SOURCE": "merge_request_event",
                "CI_MERGE_REQUEST_IID": "42",
                "CI_MERGE_REQUEST_DIFF_BASE_SHA": "gitlab-base",
                "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": "gitlab-source-head",
                "CI_COMMIT_SHA": "merged-result-head",
            },
            clear=True,
        ), patch("generate_pipeline.subprocess.run", return_value=DiffResult()):
            changed_files = generate_pipeline.changed_files_from_environment()

        config = generate_pipeline.read_config()
        services = {
            job["service"]
            for job in generate_pipeline.build_jobs(config, changed_files)
            if isinstance(job.get("service"), str)
        }
        self.assertEqual(services, {"keycloak", "traefik"})

    def test_woodpecker_changed_files_use_pipeline_metadata(self):
        changed = ["services/keycloak/config.yaml", "shared/scripts/generate_woodpecker.py"]
        with patch.dict(os.environ, {"CI_PIPELINE_FILES": json.dumps(changed)}, clear=True):
            self.assertEqual(generate_woodpecker.changed_files(), changed)

    def test_repository_validation_workflow_is_not_a_deployment_workflow(self):
        workflow = (generate_woodpecker.REPO_ROOT / ".woodpecker/validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("event: deployment", workflow)

    def test_woodpecker_rollout_includes_changed_service_configuration(self):
        targets = [
            ("woodpecker", "example-cluster"),
            ("woodpecker_agent", "example-cluster"),
        ]

        self.assertEqual(
            generate_woodpecker.affected_services(["services/woodpecker/config.yaml"], targets),
            {"woodpecker"},
        )
        self.assertEqual(
            generate_woodpecker.affected_services(["services/woodpecker_agent/config.yaml"], targets),
            {"woodpecker_agent"},
        )

    def test_global_impact_paths_select_all_services(self):
        targets = [
            ("freeipa", "example-cluster"),
            ("keycloak", "example-cluster"),
            ("netbox", "example-cluster"),
        ]
        for changed_file in (
            "environments/example/platform.yaml",
            "services/freeipa/ansible/freeipa.yml",
        ):
            with self.subTest(changed_file=changed_file):
                self.assertEqual(
                    generate_woodpecker.affected_services([changed_file], targets),
                    {"freeipa", "keycloak", "netbox"},
                )

    def test_unrelated_shared_pipeline_change_does_not_roll_out_every_service(self):
        config = generate_pipeline.read_config()
        services = {
            job["service"]
            for job in generate_pipeline.build_jobs(
                config,
                ["shared/scripts/generate_pipeline.py"],
            )
            if isinstance(job.get("service"), str)
        }
        self.assertEqual(services, set())

    def test_ci_and_woodpecker_workflow_changes_do_not_roll_out_every_service(self):
        config = generate_pipeline.read_config()
        for changed_file in (
            "ci/dagger/src/drg_ci/__init__.py",
            ".woodpecker/validate.yml",
        ):
            with self.subTest(changed_file=changed_file):
                services = {
                    job["service"]
                    for job in generate_pipeline.build_jobs(config, [changed_file])
                    if isinstance(job.get("service"), str)
                }
                self.assertEqual(services, set())

    def test_service_specific_shared_role_does_not_roll_out_every_service(self):
        targets = [
            ("woodpecker", "example-cluster"),
            ("keycloak", "example-cluster"),
            ("netbox", "example-cluster"),
        ]

        self.assertEqual(
            generate_woodpecker.affected_services(
                ["shared/ansible/roles/woodpecker_ha/tasks/main.yml"],
                targets,
            ),
            {"woodpecker"},
        )

    def test_shared_pacemaker_role_rolls_out_both_ha_services(self):
        targets = [
            ("forgejo", "example-cluster"),
            ("woodpecker", "example-cluster"),
            ("keycloak", "example-cluster"),
        ]

        self.assertEqual(
            generate_woodpecker.affected_services(
                ["shared/ansible/roles/pacemaker_ha/tasks/main.yml"],
                targets,
            ),
            {"forgejo", "woodpecker"},
        )

    def test_shared_etcd_ha_role_rolls_out_both_ha_services(self):
        targets = [
            ("forgejo", "example-cluster"),
            ("woodpecker", "example-cluster"),
            ("keycloak", "example-cluster"),
        ]

        self.assertEqual(
            generate_woodpecker.affected_services(
                ["shared/ansible/roles/etcd_ha/templates/etcd-ha-controller.sh.j2"],
                targets,
            ),
            {"forgejo", "woodpecker"},
        )

    def test_gitlab_and_woodpecker_use_the_same_rollout_service_index(self):
        config = generate_pipeline.read_config()
        targets = [
            target
            for contour in generate_woodpecker.rollout_contours()
            for target in generate_woodpecker.rollout_targets(contour)
        ]

        for changed_file in (
            "ci/dagger/src/drg_ci/__init__.py",
            "services/keycloak/config.yaml",
        ):
            with self.subTest(changed_file=changed_file):
                gitlab_services = {
                    job["service"]
                    for job in generate_pipeline.build_jobs(config, [changed_file])
                    if isinstance(job.get("service"), str)
                }
                woodpecker_services = generate_woodpecker.affected_services(
                    [changed_file], targets
                )
                self.assertEqual(gitlab_services, woodpecker_services)

    def test_generated_workflow_dependencies_are_effective(self):
        with patch.dict(
            os.environ,
            {
                "DRG_CHANGED_FILES": json.dumps(["ci/dagger/src/drg_ci/__init__.py"]),
                "CI_PIPELINE_EVENT": "push",
                "CI_COMMIT_BRANCH": "drunk",
            },
            clear=True,
        ):
            workflows = generate_woodpecker.expected_workflows()

        names = {"validate", *(Path(filename).stem for filename in workflows)}
        for filename, content in workflows.items():
            with self.subTest(filename=filename):
                document = generate_woodpecker.yaml.safe_load(content)
                self.assertIsInstance(document, dict)
                self.assertTrue(
                    set(document.get("depends_on", [])).issubset(names)
                )

    def test_pipeline_graph_contains_workflow_and_step_edges(self):
        graph = pipeline_graph.build_graph(
            event="deployment",
            branch="drunk",
            changed_files=["services/woodpecker/config.yaml"],
            deployment_target="woodpecker:example-cluster:a",
        )
        active = {
            node["label"]
            for node in graph["nodes"]
            if node["kind"] == "workflow" and node["status"] == "active"
        }
        self.assertIn("validate", active)
        self.assertIn("rollout-deploy-a", active)
        self.assertTrue(
            any(
                edge["kind"] == "workflow"
                and edge["from"] == "workflow:validate"
                and edge["to"] == "workflow:rollout-deploy-a"
                for edge in graph["edges"]
            )
        )
        self.assertTrue(any(edge["kind"] == "step" for edge in graph["edges"]))

    def test_pipeline_graph_supports_pull_request_scenarios(self):
        graph = pipeline_graph.build_graph(
            event="pull_request",
            branch="feature/woodpecker",
            changed_files=["services/keycloak/config.yaml"],
        )
        active = {
            node["label"]
            for node in graph["nodes"]
            if node["kind"] == "workflow" and node["status"] == "active"
        }
        self.assertIn("validate", active)
        self.assertTrue(any(name.startswith("rollout-") for name in active))
        self.assertFalse(any(item["level"] == "error" for item in graph["diagnostics"]))

    def test_pipeline_graph_reports_validation_only_scenario(self):
        graph = pipeline_graph.build_graph(
            event="push",
            branch="drunk",
            changed_files=["README.md"],
        )
        self.assertIn(
            "validate",
            {
                node["label"]
                for node in graph["nodes"]
                if node["status"] == "active"
            },
        )
        self.assertTrue(any(node["status"] == "skipped" for node in graph["nodes"]))
        self.assertTrue(
            any("only validation" in item["message"] for item in graph["diagnostics"])
        )

    def test_woodpecker_rollout_targets_include_ci_services(self):
        targets = set(generate_woodpecker.rollout_targets("A"))

        self.assertIn(("woodpecker", "example-cluster"), targets)
        self.assertIn(("woodpecker_agent", "example-cluster"), targets)

    def test_expected_workflows_plan_normals_and_split_deployment_events(self):
        environment = {"DRG_CHANGED_FILES": json.dumps(["services/woodpecker/config.yaml"])}
        with patch.dict(os.environ, {**environment, "CI_PIPELINE_EVENT": "push"}, clear=True):
            plan_workflows = generate_woodpecker.expected_workflows()
        with patch.dict(
            os.environ,
            {
                **environment,
                "CI_PIPELINE_EVENT": "deployment",
                "CI_COMMIT_BRANCH": "drunk",
                "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:a",
                "CI_PIPELINE_DEPLOY_TASK": "apply",
            },
            clear=True,
        ):
            deployment_workflows = generate_woodpecker.expected_workflows()

        self.assertTrue(any(name.startswith("rollout-") for name in plan_workflows))
        self.assertIn("rollout-a.yml", plan_workflows)
        self.assertNotIn("rollout-b.yml", plan_workflows)
        self.assertIn("post-rollout-plan.yml", plan_workflows)
        self.assertTrue(any(name.startswith("rollout-deploy-") for name in deployment_workflows))
        self.assertNotIn("rollout-b.yml", deployment_workflows)
        self.assertIn("post-rollout-deploy.yml", deployment_workflows)
        self.assertNotIn("post-rollout-plan.yml", deployment_workflows)
        self.assertIn("rollout-deploy-a.yml", deployment_workflows)
        self.assertNotIn("rollout-deploy-b.yml", deployment_workflows)
        deploy_workflows = {
            name: content
            for name, content in deployment_workflows.items()
            if name.startswith("rollout-deploy-") or name == "post-rollout-deploy.yml"
        }
        self.assertTrue(all("openbao_deploy_token" in content for content in deploy_workflows.values()))
        self.assertTrue(all("openbao_plan_token" not in content for content in deploy_workflows.values()))

        rollout_plan_workflows = {
            name: content
            for name, content in plan_workflows.items()
            if name.startswith("rollout-")
        }
        self.assertTrue(all("name: apply" not in content for content in rollout_plan_workflows.values()))
        self.assertTrue(all("approval" not in content for content in plan_workflows.values()))
        self.assertTrue(all("openbao_deploy_token" not in content for content in plan_workflows.values()))

    def test_deployment_target_is_required_and_must_be_affected(self):
        changed = {"DRG_CHANGED_FILES": json.dumps(["services/woodpecker/config.yaml"])}
        base = {
            **changed,
            "CI_PIPELINE_EVENT": "deployment",
            "CI_COMMIT_BRANCH": "drunk",
        }
        with patch.dict(os.environ, base, clear=True):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

        with patch.dict(
            os.environ,
            {**base, "CI_PIPELINE_DEPLOY_TARGET": "forgejo:example-cluster:a"},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

        with patch.dict(
            os.environ,
            {**base, "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:c"},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

        with patch.dict(
            os.environ,
            {
                **base,
                "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:b",
                "CI_PIPELINE_DEPLOY_TASK": "apply",
            },
            clear=True,
        ):
            deployment_workflows = generate_woodpecker.expected_workflows()
        self.assertNotIn("rollout-deploy-a.yml", deployment_workflows)
        self.assertIn("rollout-deploy-b.yml", deployment_workflows)
        rollout_workflows = {
            name: content
            for name, content in deployment_workflows.items()
            if name.startswith("rollout-deploy-")
        }
        self.assertTrue(all("SERVICE: woodpecker" in content for content in rollout_workflows.values()))
        self.assertTrue(all("CLUSTER: example-cluster" in content for content in rollout_workflows.values()))

        with patch.dict(
            os.environ,
            {**base, "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:a", "CI_COMMIT_BRANCH": "main"},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

    def test_woodpecker_post_rollout_is_separate_from_service_matrix(self):
        rollout = generate_woodpecker.render_workflow(
            "B",
            "rollout-a",
            [("woodpecker", "example-cluster")],
            files=["services/woodpecker/config.yaml"],
        )
        self.assertNotIn("ha-sync", rollout)
        self.assertNotIn("name: apply", rollout)
        self.assertIn('CI_PIPELINE_EVENT == "pull_request"', rollout)

        deployment = generate_woodpecker.render_workflow(
            "B",
            "rollout-deploy-a",
            [("woodpecker", "example-cluster")],
            deployment=True,
            files=["services/woodpecker/config.yaml"],
        )
        self.assertIn('CI_PIPELINE_EVENT == "deployment"', deployment)
        self.assertIn("name: apply", deployment)
        self.assertIn("openbao_deploy_token", deployment)

        plan_post_rollout = generate_woodpecker.render_post_rollout_workflow(
            "rollout-b",
            [{"job_id": "forgejo:ha-sync:example-cluster", "deployment_contour": "B"}],
            "B",
            True,
            True,
            ["forgejo:example-cluster:a"],
            files=["services/forgejo/config.yaml"],
        )
        self.assertNotIn("ha-sync", plan_post_rollout)
        self.assertNotIn("traefik-apply", plan_post_rollout)
        self.assertIn("openbao_plan_token", plan_post_rollout)

        post_rollout = generate_woodpecker.render_post_rollout_workflow(
            "rollout-deploy-b",
            [{"job_id": "forgejo:ha-sync:example-cluster", "deployment_contour": "B"}],
            "B",
            True,
            True,
            ["forgejo:example-cluster:a"],
            deployment=True,
            files=["services/forgejo/config.yaml"],
        )
        self.assertIsInstance(generate_woodpecker.yaml.safe_load(rollout), dict)
        self.assertIsInstance(generate_woodpecker.yaml.safe_load(deployment), dict)
        self.assertIsInstance(generate_woodpecker.yaml.safe_load(post_rollout), dict)
        self.assertIn("depends_on:\n  - rollout-deploy-b", post_rollout)
        self.assertIn("- name: ha-sync", post_rollout)
        self.assertIn("- name: traefik-preview", post_rollout)
        self.assertNotIn("approval", post_rollout)
        self.assertIn("- name: traefik-apply", post_rollout)
        self.assertIn("- name: dns-ingress", post_rollout)
        self.assertIn("depends_on: [ha-sync]", post_rollout)
        self.assertIn("depends_on: [traefik-apply]", post_rollout)
        self.assertIn("openbao_deploy_token", post_rollout)

    def test_deployment_workflow_is_gated_to_deployment_event_and_protected_branch(self):
        workflow = generate_woodpecker.render_workflow(
            "A",
            None,
            [("woodpecker", "example-cluster")],
            deployment=True,
            files=["services/woodpecker/config.yaml"],
        )
        document = generate_woodpecker.yaml.safe_load(workflow)

        self.assertEqual(
            document["when"]["evaluate"],
            'CI_PIPELINE_EVENT == "deployment" && (CI_COMMIT_BRANCH == "drunk" || CI_PIPELINE_PARENT != "0")',
        )
        self.assertIn("name: apply", workflow)
        self.assertIn("openbao_deploy_token", workflow)

    def test_deployment_rejects_non_apply_tasks(self):
        with patch.dict(
            os.environ,
            {
                "DRG_CHANGED_FILES": json.dumps(["services/woodpecker/config.yaml"]),
                "CI_PIPELINE_EVENT": "deployment",
                "CI_COMMIT_BRANCH": "drunk",
                "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:a",
                "CI_PIPELINE_DEPLOY_TASK": "plan",
            },
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

    def test_deployment_from_pull_request_parent_is_allowed(self):
        with patch.dict(
            os.environ,
            {
                "DRG_CHANGED_FILES": json.dumps(["services/woodpecker/config.yaml"]),
                "CI_PIPELINE_EVENT": "deployment",
                "CI_COMMIT_BRANCH": "feat/webhook",
                "CI_PIPELINE_PARENT": "135",
                "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:a",
                "CI_PIPELINE_DEPLOY_TASK": "apply",
            },
            clear=True,
        ):
            deployment_workflows = generate_woodpecker.expected_workflows()
        self.assertIn("rollout-deploy-a.yml", deployment_workflows)

    def test_deployment_without_protected_branch_or_parent_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "DRG_CHANGED_FILES": json.dumps(["services/woodpecker/config.yaml"]),
                "CI_PIPELINE_EVENT": "deployment",
                "CI_COMMIT_BRANCH": "feat/webhook",
                "CI_PIPELINE_DEPLOY_TARGET": "woodpecker:example-cluster:a",
                "CI_PIPELINE_DEPLOY_TASK": "apply",
            },
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                generate_woodpecker.expected_workflows()

    def test_manifest_adds_environment_state_and_runner_metadata(self):
        config = {
            "defaults": {"default_environment": "staging"},
            "services": {},
        }
        jobs = [
            {
                "service": "foo",
                "job_id": "foo:example-cluster:a",
                "template": "plan.yml.j2",
                "state_name": "foo-example-cluster-a",
                "runner_labels": ["forgejo-host", "rollout-target-a"],
            }
        ]
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False), patch.object(
            generate_pipeline, "read_platform", return_value={"rollout_contours": {"A": {"order": 1}}}
        ):
            manifest = generate_pipeline.forgejo_manifest(config, jobs)

        generated_job = manifest["jobs"][0]
        self.assertEqual(manifest["provider"], "forgejo")
        self.assertEqual(generated_job["deploy_environment"], "staging")
        self.assertEqual(generated_job["state_name"], "staging-foo-example-cluster-a")
        self.assertEqual(generated_job["runner_labels"], ["forgejo-host", "rollout-target-a"])
        json.dumps(manifest)

    def test_manifest_reader_rejects_wrong_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text('{"version": 2, "jobs": []}\n', encoding="utf-8")
            with self.assertRaises(SystemExit):
                run_pipeline.read_manifest(path)

    def test_ansible_collections_are_installed_into_the_configured_path(self):
        with tempfile.TemporaryDirectory() as directory:
            collection_root = Path(directory) / "collections"
            marker = Path(directory) / "requirements.sha256"
            environment = {"ANSIBLE_COLLECTIONS_PATH": "/custom/collections"}
            with patch.object(run_pipeline, "ANSIBLE_COLLECTIONS_ROOT", collection_root), patch.object(
                run_pipeline, "ANSIBLE_COLLECTIONS_MARKER", marker
            ), patch.object(run_pipeline, "run") as run_command:
                run_pipeline.ensure_ansible_collections(environment)

            self.assertTrue(collection_root.is_dir())
            self.assertEqual(
                environment["ANSIBLE_COLLECTIONS_PATH"],
                os.pathsep.join(
                    (str(collection_root), "/custom/collections", "/usr/share/ansible/collections")
                ),
            )
            self.assertEqual(run_command.call_args.args[0][:4], [
                "ansible-galaxy",
                "collection",
                "install",
                "-r",
            ])
            self.assertNotIn("--force", run_command.call_args.args[0])

    def test_ansible_collections_skip_install_when_requirements_marker_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "requirements.sha256"
            marker.write_text(
                hashlib.sha256(
                    run_pipeline.ANSIBLE_COLLECTIONS_REQUIREMENTS.read_bytes()
                ).hexdigest()
                + "\n",
                encoding="utf-8",
            )
            with patch.object(run_pipeline, "ANSIBLE_COLLECTIONS_ROOT", Path(directory) / "collections"), patch.object(
                run_pipeline, "ANSIBLE_COLLECTIONS_MARKER", marker
            ), patch.object(run_pipeline, "run") as run_command:
                run_pipeline.ensure_ansible_collections({})

            run_command.assert_not_called()

    def test_ansible_collections_reinstall_when_requirements_marker_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "requirements.sha256"
            marker.write_text("old\n", encoding="utf-8")
            with patch.object(run_pipeline, "ANSIBLE_COLLECTIONS_ROOT", Path(directory) / "collections"), patch.object(
                run_pipeline, "ANSIBLE_COLLECTIONS_MARKER", marker
            ), patch.object(run_pipeline, "run") as run_command:
                run_pipeline.ensure_ansible_collections({})

            run_command.assert_called_once()
            self.assertNotEqual(marker.read_text(encoding="utf-8").strip(), "old")


if __name__ == "__main__":
    unittest.main()
