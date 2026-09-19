#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

[[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root@pam on Proxmox" >&2; exit 2; }
[[ $# -eq 1 ]] || { echo "Usage: $0 PROXMOX_NODE_NAME" >&2; exit 2; }
node=$1
mapfile -t gpu_bdfs < <(lspci -Dnn | awk '/NVIDIA/ && /(VGA compatible controller|3D controller)/ && /RTX 3080/ {print $1}' | sort)
[[ ${#gpu_bdfs[@]} -eq 8 ]] || { echo "ERROR: expected exactly 8 RTX 3080 functions" >&2; exit 1; }

for index in "${!gpu_bdfs[@]}"; do
  bdf=${gpu_bdfs[$index]}
  mapping=llm-gpu-$index
  pci_id=$(lspci -Dnns "$bdf" | sed -n 's/.*\[\([0-9a-fA-F]\{4\}:[0-9a-fA-F]\{4\}\)\].*/\1/p' | tr '[:upper:]' '[:lower:]')
  group=$(basename "$(readlink -f "/sys/bus/pci/devices/$bdf/iommu_group")")
  subsystem_vendor=$(<"/sys/bus/pci/devices/$bdf/subsystem_vendor")
  subsystem_device=$(<"/sys/bus/pci/devices/$bdf/subsystem_device")
  [[ -n "$subsystem_vendor" && -n "$subsystem_device" ]] || {
    echo "ERROR: cannot determine subsystem ID for $bdf" >&2
    exit 1
  }
  subsystem_id="${subsystem_vendor#0x}:${subsystem_device#0x}"
  # The PCI mapping API calls this field `iommugroup` (without a hyphen).
  # `subsystem-id` is required by current Proxmox when validating mappings at VM start.
  map="node=$node,path=$bdf,id=$pci_id,iommugroup=$group,subsystem-id=$subsystem_id"
  if pvesh get "/cluster/mapping/pci/$mapping" >/dev/null 2>&1; then
    pvesh set "/cluster/mapping/pci/$mapping" --map "$map"
  else
    # Resource mappings use `description`; `comment` is not a valid pvesh
    # option for this endpoint.
    pvesh create /cluster/mapping/pci --id "$mapping" --map "$map" --description "LLM RTX 3080 $index"
  fi
  echo "$mapping -> $bdf ($pci_id, IOMMU group $group)"
done
echo 'Terraform input: ["llm-gpu-0","llm-gpu-1","llm-gpu-2","llm-gpu-3","llm-gpu-4","llm-gpu-5","llm-gpu-6","llm-gpu-7"]'
