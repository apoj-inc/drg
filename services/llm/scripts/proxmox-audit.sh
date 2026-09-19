#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "ERROR: run this read-only audit as root on the Proxmox node" >&2
  exit 2
fi
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 OUTPUT_DIRECTORY" >&2
  exit 2
fi

output=$1
mkdir -p "$output"
output=$(realpath "$output")

capture() {
  local name=$1
  shift
  { echo "+ $*"; "$@"; } >"$output/$name.txt" 2>&1 || true
}

capture pve-version pveversion -v
capture kernel uname -a
capture cpu lscpu
capture cpu-topology lscpu -e=CPU,NODE,SOCKET,CORE,ONLINE,MAXMHZ
capture numa numactl -H
capture memory free -h
capture disks lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
capture storage pvesm status
capture network ip -details address
capture routes ip route show table all
capture virtual-machines qm list
capture cluster-resources pvesh get /cluster/resources --type vm --output-format json-pretty
capture boot-tool proxmox-boot-tool status
capture kernel-command-line cat /proc/cmdline
capture grub cat /etc/default/grub
capture kernel-cmdline cat /etc/kernel/cmdline
capture firmware efibootmgr -v
capture pci lspci -Dnnk
capture pci-details lspci -Dvv
capture pci-tree lspci -Dtv
capture iommu-kernel-log sh -c "dmesg | grep -Ei 'DMAR|IOMMU|remapping'"

{
  for group in /sys/kernel/iommu_groups/*; do
    [[ -d "$group/devices" ]] || continue
    for device in "$group"/devices/*; do
      printf 'group=%s bdf=%s ' "${group##*/}" "${device##*/}"
      lspci -Dnns "${device##*/}"
    done
  done
} >"$output/iommu-groups.txt"

# lspci normally prints the PCI class before the vendor name, e.g.
# "3D controller: NVIDIA Corporation ...".  Identify NVIDIA by its PCI vendor
# ID and use the numeric display-class codes so field ordering and pci.ids text
# do not affect discovery.
gpu_lines=$(lspci -Dnn | awk '
  /\[10de:[[:xdigit:]]{4}\]/ && /\[(0300|0302)\]/ {print}
' || true)
gpu_count=$(grep -c . <<<"$gpu_lines" || true)
if [[ "$gpu_count" -ne 8 ]]; then
  echo "ERROR: expected 8 NVIDIA display/3D functions, found $gpu_count" >&2
  printf '%s\n' "$gpu_lines" >&2
  exit 1
fi
# 10de:2206 is the RTX 3080 display-function PCI ID.  Keep the name check for
# readable lspci output, but accept the ID when pci.ids is old or incomplete.
if grep -Eiv 'RTX 3080|\[10de:2206\]' <<<"$gpu_lines" >/dev/null; then
  echo "ERROR: at least one audited GPU is not reported as RTX 3080" >&2
  printf '%s\n' "$gpu_lines" >&2
  exit 1
fi

printf '%s\n' "$gpu_lines" >"$output/rtx3080-functions.txt"
printf '%s\n' \
  "Audit completed at $(date --iso-8601=seconds)" \
  "Node: $(hostname -f)" \
  "Output: $output" \
  "RTX 3080 functions: 8" | tee "$output/SUMMARY.txt"
