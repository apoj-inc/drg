# SPDX-License-Identifier: Apache-2.0
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_openbao_bootstrap_defers_forgejo_actions_auth():
    defaults = (ROOT / "ansible/roles/openbao_app/defaults/main.yml").read_text()
    tasks = (ROOT / "ansible/roles/openbao_app/tasks/declarative_config.yml").read_text()
    playbook = (ROOT.parent / "services/openbao/ansible/openbao.yml").read_text()

    assert "openbao_declarative_auth_exclude_files: []" in defaults
    assert 'excludes: "{{ openbao_declarative_auth_exclude_files }}"' in tasks
    assert "- forgejo-actions.yaml" in playbook


def test_forgejo_play_delegates_actions_auth_to_openbao():
    playbook = (ROOT.parent / "services/forgejo/ansible/forgejo.yml").read_text()
    tasks = (ROOT / "ansible/tasks/configure_forgejo_openbao_auth.yml").read_text()

    assert "configure_forgejo_openbao_auth.yml" in playbook
    assert "FORGEJO_OPENBAO_CONFIGURE_ACTIONS_AUTH" in playbook
    assert 'delegate_to: "{{ forgejo_openbao_delegate_host }}"' in tasks
    assert "Wait for Forgejo Actions OIDC discovery" in tasks
    assert "vault_write" in tasks
    assert "forgejo_openbao_config_token" in tasks
