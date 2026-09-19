# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_ci_policy_allows_read_only_openstack_platform_secret_path():
    policy = load_yaml("services/openbao/openbao-config/policies/ci.yaml")
    rules = {rule["path"]: rule["capabilities"] for rule in policy["rules"]}

    assert rules["kv/data/platform/openstack"] == ["read"]


def test_gitlab_and_forgejo_jwt_roles_use_ci_policy():
    gitlab_role = load_yaml("services/openbao/openbao-config/roles/gitlab-ci.yaml")
    forgejo_role = load_yaml("services/openbao/openbao-config/roles/forgejo-actions.yaml")

    assert gitlab_role["auth_mount"] == "gitlab-ci"
    assert gitlab_role["policy"] == "ci"
    assert forgejo_role["auth_mount"] == "forgejo-actions"
    assert forgejo_role["policy"] == "ci"


def test_jwt_roles_remain_restricted_to_expected_repository_claims():
    gitlab_auth = load_yaml("services/openbao/openbao-config/auth/gitlab-jwt.yaml")
    forgejo_auth = load_yaml("services/openbao/openbao-config/auth/forgejo-actions.yaml")

    gitlab_role = gitlab_auth["roles"][0]
    forgejo_role = forgejo_auth["roles"][0]
    assert gitlab_role["bound_claims"]["project_path"] == "dwarves/drg"
    assert forgejo_role["bound_claims"]["repository"] == "example-org/drg"
    assert gitlab_role["token_ttl"] == "15m"
    assert gitlab_role["token_max_ttl"] == "30m"
    assert forgejo_role["token_ttl"] == "15m"
    assert forgejo_role["token_max_ttl"] == "30m"
