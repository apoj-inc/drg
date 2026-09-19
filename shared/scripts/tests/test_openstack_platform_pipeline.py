# SPDX-License-Identifier: Apache-2.0
import json
import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def generated_manifest(changed_files=None):
    environment = os.environ.copy()
    if changed_files is not None:
        environment["CHANGED_FILES_OVERRIDE"] = json.dumps(changed_files)
    result = subprocess.run(
        ["python3", "shared/scripts/generate_pipeline.py", "--format", "forgejo-manifest"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_platform_graph_skips_unrelated_changes():
    manifest = generated_manifest(["services/dockerhub/config.yaml"])
    assert [job for job in manifest["jobs"] if job.get("kind") == "platform"] == []


def test_platform_graph_runs_only_affected_phases():
    manifest = generated_manifest(["platform/openstack/kolla/globals.yml.example"])
    jobs = [job for job in manifest["jobs"] if job.get("kind") == "platform"]
    assert [job["phase"] for job in jobs] == [
        "validate", "kolla-prechecks", "kolla-deploy", "smoke",
    ]
    assert all(job["manual_apply"] is False for job in jobs)
    assert jobs[1]["platform_needs"] == ["platform:openstack:example:validate"]
    assert jobs[2]["platform_needs"] == ["platform:openstack:example:kolla-prechecks"]
    assert jobs[3]["platform_needs"] == ["platform:openstack:example:kolla-deploy"]


def test_platform_graph_includes_foundation_phases_only_for_foundation_changes():
    manifest = generated_manifest(["platform/openstack/foundation/projects/services/network.yaml"])
    jobs = [job for job in manifest["jobs"] if job.get("kind") == "platform"]
    assert [job["phase"] for job in jobs] == [
        "validate", "foundation-plan", "foundation-apply", "smoke"
    ]
    assert jobs[1]["platform_needs"] == ["platform:openstack:example:validate"]
    assert jobs[2]["platform_needs"] == ["platform:openstack:example:foundation-plan"]
    assert jobs[3]["platform_needs"] == ["platform:openstack:example:foundation-apply"]
    assert jobs[1]["foundation_plan_artifacts"] == [
        "platform/openstack/foundation/projects/services/identity",
        "platform/openstack/foundation/projects/services/network",
        "platform/openstack/foundation/projects/services/security",
        "platform/openstack/foundation/projects/services/compute-catalog",
        "platform/openstack/foundation/projects/services/images",
    ]


def test_platform_openbao_specs_use_kv_v2_path_without_secret_values():
    manifest = generated_manifest()
    job = next(job for job in manifest["jobs"] if job.get("kind") == "platform")
    specs = job["openbao"]["secrets"]
    assert {spec["path"] for spec in specs} >= {"platform/openstack"}
    assert {spec["path"] for spec in specs} <= {"platform/openstack", "example-cluster"}
    assert {spec["mount"] for spec in specs} == {"kv"}
    assert next(spec for spec in specs if spec["field"] == "kolla_passwords")["file"] is True
    assert next(spec for spec in specs if spec["field"] == "kolla_passwords")["encoding"] == "base64"
    private_key = next(spec for spec in specs if spec["field"] == "ansible_private_key_file")
    assert private_key["file"] is True
    assert private_key["encoding"] == "plain"
    assert all(spec["job_types"] == ["platform"] for spec in specs)
    assert all("value" not in spec for spec in specs)


def test_file_secret_behavior_is_declared_by_platform_configuration():
    manifest = generated_manifest()
    job = next(job for job in manifest["jobs"] if job.get("kind") == "platform")
    kolla_passwords = next(spec for spec in job["openbao"]["secrets"] if spec["field"] == "kolla_passwords")
    client_secret = next(
        spec for spec in job["openbao"]["secrets"]
        if spec["field"] == "authentik_openstack_client_secret"
    )
    assert kolla_passwords["file"] is True
    assert kolla_passwords["encoding"] == "base64"
    assert client_secret["file"] is False
    assert client_secret["encoding"] == "plain"


def test_platform_config_is_valid_yaml_and_has_required_sections():
    platform = yaml.safe_load(
        (ROOT / "environments/example/platform.yaml").read_text(encoding="utf-8")
    )
    openstack = platform["openstack"]
    assert openstack["enabled"] is True
    assert openstack["manual_apply"] is False
    assert openstack["dns"] == {
        "zone": "example.test",
        "ttl": 60,
        "records": [
            {
                "hostname": "openstack.example.test",
                "record_type": "CNAME",
                "target": "traefik.example.test",
            },
            {
                "hostname": "horizon.example.test",
                "record_type": "CNAME",
                "target": "traefik.example.test",
            },
            {
                "hostname": "skyline.example.test",
                "record_type": "CNAME",
                "target": "traefik.example.test",
            },
        ],
    }
    assert openstack["host"]["virtualization"]["device"] == "/dev/kvm"
    assert openstack["foundation"]["components"] == [
        "identity", "network", "security", "compute-catalog", "images"
    ]
    assert openstack["foundation"]["auth_url"] == "https://openstack.example.test/v3"
    assert openstack["foundation"]["region"] == "RegionOne"
    assert openstack["secrets"]["path"] == "kv/platform/openstack"


def test_openstack_foundation_tenant_mtu_fits_neutron_backend_limit():
    network = yaml.safe_load(
        (ROOT / "platform/openstack/foundation/projects/services/network.yaml").read_text(encoding="utf-8")
    )
    assert network["network"]["tenant"]["mtu"] == 1440
    assert network["network"]["tenant"]["mtu"] <= 1442


def test_openstack_project_declares_all_service_quotas_and_security_scope():
    identity = yaml.safe_load(
        (ROOT / "platform/openstack/foundation/projects/services/identity.yaml").read_text(encoding="utf-8")
    )
    project = identity["identity"]
    assert set(project["compute_quota"]) == {
        "cores", "instances", "ram", "key_pairs", "server_groups", "server_group_members"
    }
    assert set(project["network_quota"]) == {
        "network", "subnet", "port", "router", "floatingip",
        "security_group", "security_group_rule"
    }
    assert set(project["volume_quota"]) == {
        "volumes", "snapshots", "gigabytes", "backups", "backup_gigabytes"
    }
    security = yaml.safe_load(
        (ROOT / "platform/openstack/foundation/projects/services/security.yaml").read_text(encoding="utf-8")
    )
    assert "project_name" not in security["security"]


def test_security_group_module_accepts_explicit_project_scope():
    module = (ROOT / "shared/terraform/openstack_security_group/main.tf").read_text(encoding="utf-8")
    variables = (ROOT / "shared/terraform/openstack_security_group/variables.tf").read_text(encoding="utf-8")
    template = (ROOT / "platform/openstack/foundation/templates/security/main.tf.j2").read_text(
        encoding="utf-8"
    )
    assert 'tenant_id   = var.project_id' in module
    assert 'variable "project_id"' in variables
    assert 'data.openstack_identity_project_v3.project.id' in template


def test_network_module_assigns_subnets_to_selected_project():
    module = (ROOT / "shared/terraform/openstack_network/main.tf").read_text(encoding="utf-8")
    subnet_resource = module.split('resource "openstack_networking_subnet_v2" "this"', 1)[1]
    assert "tenant_id       = var.project_id" in subnet_resource.split('resource "openstack_networking_router_v2"', 1)[0]


def test_foundation_projects_grant_admin_access():
    module = (ROOT / "shared/terraform/openstack_project/main.tf").read_text(encoding="utf-8")
    assert 'data "openstack_identity_user_v3" "admin"' in module
    assert 'data "openstack_identity_role_v3" "admin"' in module
    assert 'resource "openstack_identity_role_assignment_v3" "admin"' in module
    assert 'project_id = openstack_identity_project_v3.this.id' in module


def test_project_foundation_resources_use_selected_project_scope():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    network_template = (ROOT / "platform/openstack/foundation/templates/network/main.tf.j2").read_text(
        encoding="utf-8"
    )
    assert "explicit project_id" in platform_runner
    assert 'name = {{ config.project_name | tf_string }}' in network_template
    assert 'data.openstack_identity_project_v3.project.id' in network_template
    assert network_template.count("data.openstack_identity_project_v3.project.id") == 1


def test_foundation_reuses_kolla_provider_network():
    network_template = (ROOT / "platform/openstack/foundation/templates/network/main.tf.j2").read_text(
        encoding="utf-8"
    )
    outputs_template = (ROOT / "platform/openstack/foundation/templates/_outputs.tf.j2").read_text(
        encoding="utf-8"
    )
    assert 'data "openstack_networking_network_v2" "external"' in network_template
    assert 'name = {{ config.network.external.name | tf_string }}' in network_template
    assert "external = true" in network_template
    assert 'external_network_id = data.openstack_networking_network_v2.external.id' in network_template
    assert 'module "external"' not in network_template
    assert 'output "external_network_id" { value = data.openstack_networking_network_v2.external.id }' in outputs_template
    assert 'output "external_network_id" { value = module.external.network_id }' not in outputs_template


def test_shared_collections_include_kolla_container_engine_modules():
    requirements = yaml.safe_load(
        (ROOT / "shared/ansible/collections/requirements.yml").read_text(encoding="utf-8")
    )
    assert any(item.get("name") == "containers.podman" for item in requirements["collections"])


def test_shared_community_general_version_is_compatible_with_kolla():
    requirements = yaml.safe_load(
        (ROOT / "shared/ansible/collections/requirements.yml").read_text(encoding="utf-8")
    )
    community_general = next(
        item for item in requirements["collections"]
        if item.get("name") == "community.general"
    )
    assert community_general["version"] == "<12"


def test_gitlab_ansible_jobs_cache_collections_without_force_reinstall():
    gitlab_template = (ROOT / "shared/templates/gitlab-ci-template.yml").read_text(encoding="utf-8")
    validate_template = (ROOT / "shared/templates/pipeline/validate.yml.j2").read_text(encoding="utf-8")
    traefik_template = (ROOT / "shared/templates/pipeline/traefik-validate.yml.j2").read_text(encoding="utf-8")

    assert "ansible-galaxy collection install --force" not in gitlab_template
    assert gitlab_template.count("shared/ansible/collections/requirements.yml") >= 2
    assert gitlab_template.count(".ansible/collections/") >= 2
    assert ".ci-kolla-ansible/" in gitlab_template
    assert ".kolla-requirements.sha256" in gitlab_template
    assert ".ansible/collections/" in validate_template
    assert ".ansible/collections" in traefik_template
    assert "shared/ansible/collections" not in traefik_template.split("--collections-path", 1)[-1]


def test_open_to_fu_provider_cache_is_configured_for_all_ci_runtimes():
    gitlab_template = (ROOT / "shared/templates/gitlab-ci-template.yml").read_text(encoding="utf-8")
    validate_template = (ROOT / "shared/templates/pipeline/validate.yml.j2").read_text(encoding="utf-8")
    dagger_module = (ROOT / "ci/dagger/src/drg_ci/__init__.py").read_text(encoding="utf-8")
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")

    assert 'TF_PLUGIN_CACHE_DIR: "${CI_PROJECT_DIR}/.terraform.d/plugin-cache"' in gitlab_template
    # Kolla jobs no longer archive the OpenTofu provider cache; Terraform jobs
    # retain it through their dedicated cache definitions.
    assert gitlab_template.count(".terraform.d/plugin-cache/") >= 2
    assert ".terraform.d/plugin-cache/" in validate_template
    assert 'dag.cache_volume("drg-tofu-provider-cache")' in dagger_module
    assert 'env["TF_PLUGIN_CACHE_DIR"]' in platform_runner


def test_kolla_collection_cache_is_validated_before_skipping_install():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    assert "kolla_module_probe" in platform_runner
    assert "collections_ready" in platform_runner
    assert '"--force"' in platform_runner
    assert '"ansible-config"' in platform_runner
    assert '"community.general.modprobe"' in platform_runner
    assert platform_runner.count('"-r", str(kolla_share / "requirements-core.yml")') == 1
    assert platform_runner.count('"-r", str(kolla_share / "requirements.yml")') == 1


def test_kolla_runtime_uses_generated_inventory_and_debug_only_diagnostics():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    assert "generate_kolla_inventory" in platform_runner
    assert 'inventory_path = config_dir / "inventory"' in platform_runner
    assert "service_families" in platform_runner
    assert 'base_name.startswith(f\"{service}-\")' in platform_runner
    assert '"ironic-neutron-agent"' in platform_runner
    assert '"manila-share"' in platform_runner
    assert 'os.environ.get("KOLLA_DEBUG"' in platform_runner
    assert 'os.environ.get("CI_DEBUG_TRACE"' in platform_runner


def test_kolla_deploy_applies_skyline_internal_catalog_config():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    skyline_config = (ROOT / "platform/openstack/kolla/skyline/skyline.yaml").read_text(
        encoding="utf-8"
    )
    assert 'platform/openstack/kolla/skyline/skyline.yaml' in platform_runner
    assert 'config_dir / "config" / "skyline" / "skyline.yaml"' in platform_runner
    assert "interface_type: internal" in skyline_config


def test_kolla_deploy_uses_pull_only_cache_policy():
    gitlab_template = (ROOT / "shared/templates/gitlab-ci-template.yml").read_text(encoding="utf-8")
    phase_template = (ROOT / "shared/templates/pipeline/platform-phase.yml.j2").read_text(encoding="utf-8")
    assert "PLATFORM_CACHE_POLICY: \"pull-push\"" in gitlab_template
    assert "PLATFORM_CACHE_POLICY: pull" in phase_template
    assert ".terraform.d/plugin-cache/" not in gitlab_template.split(".platform_configure:", 1)[1]


def test_kolla_deploy_flushes_memcached_only_when_secret_key_changes():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    assert "ensure_memcache_secret_key" in platform_runner
    assert 'passwords.get("memcache_secret_key")' in platform_runner
    assert "/etc/kolla/.drg-memcache-secret.sha256" in platform_runner
    assert "memcache_cache=flushed" in platform_runner
    assert "docker exec memcached perl" in platform_runner
    assert 'if phase == "kolla-deploy":' in platform_runner
    assert platform_runner.index('env["ANSIBLE_REMOTE_USER"] = env.get("ANSIBLE_USER") or "root"') < platform_runner.index(
        "ensure_memcache_secret_key(virtualenv, inventory_path, passwords_file, env)"
    )


def test_foundation_apply_refreshes_plan_credentials_before_apply():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    apply_block = platform_runner.split('if phase == "foundation-plan":', 1)[1]
    assert 'run(["tofu", "plan", "-out=tfplan"], cwd=root, env=component_env)' in apply_block
    assert 'run(["tofu", "apply", "-auto-approve", "tfplan"], cwd=root, env=component_env)' in apply_block


def test_openstack_foundation_bypasses_proxy_for_internal_vip():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    assert "add_no_proxy_hosts" in platform_runner
    assert 'openstack.get("kolla", {}).get("internal_vip", "")' in platform_runner
    assert 'env["NO_PROXY"] = value' in platform_runner
    assert 'env["no_proxy"] = value' in platform_runner


def test_openstack_smoke_uses_kolla_cli_and_admin_credentials():
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    smoke_block = platform_runner.split("def smoke(openstack:", 1)[1].split("\ndef main", 1)[0]
    assert 'shutil.which("openstack")' in smoke_block
    assert "install python3-openstackclient on the GitLab Runner" in smoke_block
    assert '"python-openstackclient"' not in smoke_block
    assert 'passwords.get("keystone_admin_password")' in smoke_block
    assert '"OS_INTERFACE": "internal"' in smoke_block
    assert 'run([client, "token", "issue"], env=env)' in smoke_block
    assert '[client, "service", "list", "-f", "json"]' in smoke_block
    assert '"keystone": {"keystone", "identity"}' in smoke_block
    assert '"cinder": {"cinder", "volume", "volumev2", "volumev3"}' in smoke_block
    assert 'service_aliases.get(service, {service}) & available_services' in smoke_block
    assert '"--service"' not in smoke_block


def test_gitlab_runner_provides_openstack_client_for_smoke():
    runner_install = (ROOT / "shared/ansible/roles/gitlab_runner_app/tasks/install.yml").read_text(
        encoding="utf-8"
    )
    platform_runner = (ROOT / "shared/scripts/run_platform.py").read_text(encoding="utf-8")
    assert "python3-openstackclient" in runner_install
    assert 'shutil.which("openstack")' in platform_runner


def test_openstack_oidc_metadata_has_provider_client_and_conf_files():
    metadata_root = ROOT / "platform/openstack/kolla/federation/metadata"
    metadata_name = "auth.example.test%2Fapplication%2Fo%2Fopenstack-keystone"
    assert all((metadata_root / f"{metadata_name}{suffix}").is_file() for suffix in (".provider", ".client", ".conf"))
