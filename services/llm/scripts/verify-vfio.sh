#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

[[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root on Proxmox" >&2; exit 2; }
grep -qw intel_iommu=on /proc/cmdline || { echo "ERROR: intel_iommu=on is absent" >&2; exit 1; }
grep -qw iommu=pt /proc/cmdline || { echo "ERROR: iommu=pt is absent" >&2; exit 1; }

count=0
while read -r bdf; do
  count=$((count + 1))
  driver=$(basename "$(readlink -f "/sys/bus/pci/devices/$bdf/driver")")
  [[ "$driver" == vfio-pci ]] || { echo "ERROR: $bdf uses $driver" >&2; exit 1; }
  group_link=/sys/bus/pci/devices/$bdf/iommu_group
  [[ -L "$group_link" ]] || { echo "ERROR: $bdf has no IOMMU group" >&2; exit 1; }
  printf '%s group=%s driver=%s\n' "$bdf" "$(basename "$(readlink -f "$group_link")")" "$driver"
done < <(lspci -Dnn | awk '/NVIDIA/ && /(VGA compatible controller|3D controller)/ && /RTX 3080/ {print $1}')
[[ "$count" -eq 8 ]] || { echo "ERROR: expected 8 RTX 3080 devices, found $count" >&2; exit 1; }
echo "VFIO verification passed for all eight RTX 3080 GPU functions."
