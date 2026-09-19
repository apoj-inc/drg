# SPDX-License-Identifier: Apache-2.0
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_forgejo_ha_brings_up_drbd_on_all_candidates():
    tasks = (ROOT / "ansible/roles/forgejo_ha/tasks/main.yml").read_text()
    unit = (ROOT / "ansible/roles/forgejo_ha/templates/forgejo-drbd.service.j2").read_text()
    control = (ROOT / "ansible/roles/forgejo_ha/templates/forgejo-ha-control.sh.j2").read_text()

    assert "Bring up the Forgejo DRBD resource on every candidate" in tasks
    assert "state: started" in tasks
    assert "drbdadm up {{ forgejo_ha_drbd_resource }}" in unit
    assert "drbdadm connect {{ forgejo_ha_drbd_resource }}" in unit
    assert "drbdadm up \"${resource}\"" in control
    assert "drbdadm connect \"${resource}\"" in control
    assert "DRBD resource ${resource} is not Connected; refusing Forgejo startup" in control
    assert "wait_for_replication" in control


def test_forgejo_uses_the_dedicated_scsi1_disk_for_drbd():
    config = (ROOT.parent / "services/forgejo/config.yaml").read_text()
    assert "backing_device: /dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_drive-scsi1" in config


def test_forgejo_drbd_device_check_follows_udev_symlink():
    tasks = (ROOT / "ansible/roles/forgejo_ha/tasks/main.yml").read_text()
    assert "path: \"{{ forgejo_ha_drbd_backing_device }}\"\n    follow: true" in tasks


def test_forgejo_ha_mounts_drbd_explicitly_without_fstab():
    control = (ROOT / "ansible/roles/forgejo_ha/templates/forgejo-ha-control.sh.j2").read_text()
    assert 'filesystem={{ forgejo_ha_filesystem | quote }}' in control
    assert '/usr/bin/mount -t "${filesystem}" "${device}" "${mountpoint}"' in control
    assert '/usr/bin/mount "${device}"' not in control


def test_runner_registers_only_against_the_active_forgejo_candidate():
    tasks = (ROOT / "ansible/roles/forgejo_runner_app/tasks/main.yml").read_text()
    playbook = (ROOT.parent / "services/forgejo_runner/ansible/forgejo_runner.yml").read_text()

    assert "Probe Forgejo candidates for the active instance" in tasks
    assert "127.0.0.1:3000/api/healthz" in tasks
    assert "item.rc | default(1) | int == 0" in tasks
    assert 'forgejo_runner_registration_delegate_host: "{{ item.item }}"' in tasks
    assert "default('localhost', true)" in tasks
    assert "not forgejo_runner_managed_registration.stat.exists" in tasks
    assert "forgejo_runner_registration_delegate_host_override" in playbook
    assert "groups['tags_forgejo'] | first" not in playbook


def test_powerdns_recursor_has_an_automatic_recovery_watchdog():
    configure = (ROOT / "ansible/roles/powerdns_app/tasks/configure.yml").read_text()
    lifecycle = (ROOT / "ansible/roles/powerdns_app/tasks/main.yml").read_text()
    handlers = (ROOT / "ansible/roles/powerdns_app/handlers/main.yml").read_text()
    watchdog = (ROOT / "ansible/roles/powerdns_app/templates/powerdns-recursor-watchdog.sh.j2").read_text()
    timer = (ROOT / "ansible/roles/powerdns_app/templates/powerdns-recursor-watchdog.timer.j2").read_text()

    assert "Install PowerDNS Recursor watchdog units" in configure
    assert lifecycle.index("Stop PowerDNS Recursor watchdog during deployment") < lifecycle.index("Run PowerDNS Compose lifecycle")
    assert lifecycle.index("Enable PowerDNS Recursor watchdog after deployment") > lifecycle.index("Run PowerDNS Compose lifecycle")
    assert "Stop PowerDNS Recursor watchdog before handler restart" in handlers
    assert "Start PowerDNS Recursor watchdog after handler restart" in handlers
    assert "--force-recreate powerdns-recursor" in watchdog
    assert "OnUnitActiveSec=30s" in timer
