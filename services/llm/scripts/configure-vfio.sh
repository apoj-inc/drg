#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

usage() {
  echo "Usage: $0 check|apply" >&2
  echo "Discovers all functions of exactly 8 RTX 3080 cards; apply updates boot/VFIO config." >&2
}
[[ $# -eq 1 ]] || { usage; exit 2; }
action=$1
[[ "$action" == check || "$action" == apply ]] || { usage; exit 2; }
[[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root on Proxmox" >&2; exit 2; }

mapfile -t gpu_bdfs < <(lspci -Dnn | awk '/NVIDIA/ && /(VGA compatible controller|3D controller)/ && /RTX 3080/ {print $1}')
[[ ${#gpu_bdfs[@]} -eq 8 ]] || { echo "ERROR: expected exactly 8 RTX 3080 functions" >&2; exit 1; }

declare -A related=()
for gpu in "${gpu_bdfs[@]}"; do
  slot=${gpu%.*}
  while read -r bdf _; do related["$bdf"]=1; done < <(lspci -Dnn | awk -v slot="$slot" '$1 ~ ("^" slot "\\.")')
done

declare -A ids=()
for bdf in "${!related[@]}"; do
  pci_id=$(lspci -Dnns "$bdf" | sed -n 's/.*\[\([0-9a-fA-F]\{4\}:[0-9a-fA-F]\{4\}\)\].*/\1/p' | tr '[:upper:]' '[:lower:]')
  [[ -n "$pci_id" ]] || { echo "ERROR: cannot determine PCI ID for $bdf" >&2; exit 1; }
  ids["$pci_id"]=1
  group_link=/sys/bus/pci/devices/$bdf/iommu_group
  [[ -L "$group_link" ]] || { echo "ERROR: $bdf has no IOMMU group" >&2; exit 1; }
  group=$(basename "$(readlink -f "$group_link")")
  for member in /sys/kernel/iommu_groups/"$group"/devices/*; do
    member_bdf=${member##*/}
    [[ -n ${related[$member_bdf]:-} ]] || {
      echo "ERROR: IOMMU group $group contains non-GPU-slot device $member_bdf" >&2
      echo "Do not enable ACS override automatically; correct physical placement first." >&2
      exit 1
    }
  done
done

vfio_ids=$(printf '%s\n' "${!ids[@]}" | sort | paste -sd, -)
echo "GPU functions: ${gpu_bdfs[*]}"
echo "All slot functions: $(printf '%s\n' "${!related[@]}" | sort | paste -sd, -)"
echo "VFIO PCI IDs: $vfio_ids"
grep -qw intel_iommu=on /proc/cmdline && iommu_on=yes || iommu_on=no
echo "Current kernel has intel_iommu=on: $iommu_on"
[[ "$action" == check ]] && exit 0

backup=/var/lib/llm-vfio-backup/$(date +%Y%m%dT%H%M%S)
mkdir -p "$backup"
paths=(/etc/default/grub /etc/kernel/cmdline /etc/modules-load.d/llm-vfio.conf /etc/modprobe.d/llm-vfio.conf /etc/modprobe.d/llm-nvidia-blacklist.conf)
for path in "${paths[@]}"; do
  key=${path#/}
  if [[ -e "$path" ]]; then
    mkdir -p "$backup/$(dirname "$key")"
    cp -a "$path" "$backup/$key"
  else
    mkdir -p "$backup/absent/$(dirname "$key")"
    : >"$backup/absent/$key"
  fi
done

append_kernel_parameters() {
  local line=$1 parameter
  for parameter in intel_iommu=on iommu=pt; do
    [[ " $line " == *" $parameter "* ]] || line="$line $parameter"
  done
  printf '%s' "${line# }"
}

boot_method=
if [[ -s /etc/kernel/cmdline ]] && command -v proxmox-boot-tool >/dev/null && proxmox-boot-tool status >/dev/null 2>&1; then
  boot_method=proxmox-boot-tool
  old=$(head -n1 /etc/kernel/cmdline)
  append_kernel_parameters "$old" >"$backup/kernel-cmdline.new"
  install -m 0644 "$backup/kernel-cmdline.new" /etc/kernel/cmdline
elif [[ -r /etc/default/grub ]]; then
  boot_method=grub
  old=$(sed -n 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/\1/p' /etc/default/grub)
  [[ -n "$old" || $(grep -c '^GRUB_CMDLINE_LINUX_DEFAULT=""' /etc/default/grub) -eq 1 ]] || {
    echo "ERROR: cannot safely parse GRUB_CMDLINE_LINUX_DEFAULT" >&2
    exit 1
  }
  new=$(append_kernel_parameters "$old")
  sed "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\"$new\"|" /etc/default/grub >"$backup/grub.new"
  install -m 0644 "$backup/grub.new" /etc/default/grub
else
  echo "ERROR: neither a managed /etc/kernel/cmdline nor GRUB configuration was detected" >&2
  exit 1
fi

# Write the recovery manifest before changing any boot or module files so an
# interrupted apply remains rollbackable.
printf '%s\n' \
  "BACKUP=$backup" \
  "BOOT_METHOD=$boot_method" \
  "VFIO_PCI_IDS=$vfio_ids" \
  "GPU_BDFS=${gpu_bdfs[*]}" >"$backup/manifest"

printf '%s\n' vfio vfio_pci vfio_iommu_type1 >/etc/modules-load.d/llm-vfio.conf
printf 'options vfio-pci ids=%s disable_vga=1\n' "$vfio_ids" >/etc/modprobe.d/llm-vfio.conf
: >/etc/modprobe.d/llm-nvidia-blacklist.conf
for module in nouveau nvidia nvidia_drm nvidia_modeset nvidia_uvm; do
  if modinfo "$module" >/dev/null 2>&1; then
    printf 'blacklist %s\n' "$module" >>/etc/modprobe.d/llm-nvidia-blacklist.conf
  fi
done

update-initramfs -u -k all
if [[ "$boot_method" == proxmox-boot-tool ]]; then
  proxmox-boot-tool refresh
else
  update-grub
fi

printf '%s\n' \
  "BACKUP=$backup" \
  "BOOT_METHOD=$boot_method" \
  "VFIO_PCI_IDS=$vfio_ids" \
  "GPU_BDFS=${gpu_bdfs[*]}" >"$backup/manifest"
echo "Configuration written. Backup: $backup"
echo "Reboot from an out-of-band-capable maintenance window, then run verify-vfio.sh."
