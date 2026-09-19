#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

[[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root on Proxmox" >&2; exit 2; }
[[ $# -eq 1 ]] || { echo "Usage: $0 /var/lib/llm-vfio-backup/TIMESTAMP" >&2; exit 2; }
backup=$(realpath "$1")
[[ "$backup" == /var/lib/llm-vfio-backup/* && -r "$backup/manifest" ]] || {
  echo "ERROR: invalid LLM VFIO backup directory" >&2
  exit 2
}

paths=(etc/default/grub etc/kernel/cmdline etc/modules-load.d/llm-vfio.conf etc/modprobe.d/llm-vfio.conf etc/modprobe.d/llm-nvidia-blacklist.conf)
for relative in "${paths[@]}"; do
  target=/$relative
  if [[ -e "$backup/$relative" ]]; then
    install -D -m 0644 "$backup/$relative" "$target"
  elif [[ -e "$backup/absent/$relative" ]]; then
    rm -f -- "$target"
  else
    echo "ERROR: backup has no state marker for $target" >&2
    exit 1
  fi
done

update-initramfs -u -k all
if grep -q '^BOOT_METHOD=proxmox-boot-tool$' "$backup/manifest"; then
  proxmox-boot-tool refresh
else
  update-grub
fi
echo "VFIO configuration restored from $backup. Reboot to activate the rollback."
