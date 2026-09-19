# Installation

## 1. Audit the host first

Run on the physical Proxmox node as root and retain the output as a deployment
artifact:

```bash
services/llm/scripts/proxmox-audit.sh /root/llm-audit-$(date +%Y%m%dT%H%M%S)
services/llm/scripts/configure-vfio.sh check
```

Review CPU/NUMA, storage, network, existing VM IDs, firmware, Proxmox/kernel,
all PCI IDs, and every IOMMU group. Firmware must have Intel VT-x, Intel VT-d,
Above 4G Decoding and UEFI enabled, with CSM disabled. Resizable BAR is
optional. Do not change firmware from automation.

If a GPU IOMMU group contains an unrelated device, stop and fix the physical
topology. `pcie_acs_override` is not enabled by this PR because it weakens
isolation.

## 2. Enable VFIO on the host

After reviewing the audit, run the explicit write operation in a maintenance
window:

```bash
services/llm/scripts/configure-vfio.sh apply
reboot
services/llm/scripts/verify-vfio.sh
```

The script detects GRUB versus `proxmox-boot-tool`, preserves existing kernel
parameters, writes only dedicated `llm-*` files, refreshes initramfs/boot
entries and stores a rollback directory under `/var/lib/llm-vfio-backup`.

Create one cluster PCI mapping per GPU as `root@pam` (the Proxmox API does not
permit non-PAM accounts to create these mappings):

```bash
services/llm/scripts/create-pci-mappings.sh pve-node-a
pvesh get /cluster/mapping/pci
```

The generated mapping names are `llm-gpu-0` through `llm-gpu-7`. The map path
is the audited GPU function; all related functions are bound to `vfio-pci` on
the host. If HDMI audio must be visible in the guest, add its audited function
as a separate mapping only after confirming that the VM/provider hostpci limit
and the IOMMU group layout permit it.

The script is safe to rerun: existing mapping IDs are updated and missing IDs
are created. If a previous run stopped with `Unknown option: comment`, rerun
the corrected script; no mapping was created for the failed request. The script
also records each device's subsystem ID, which current Proxmox releases require
when validating a mapping during VM startup. If startup reports a missing
`subsystem-id`, rerun this script on the Proxmox node to update all eight maps.

## 3. Provision the VM

The deployment VM ID, sizing, NUMA placement, and PCI mapping names are
populated in `services/llm/config.yaml` from the live audit. Review those
values before planning; they define VM `104`, 8 vCPUs, 64 GiB RAM, NUMA node 0,
200 GiB storage, and four `llm-gpu-*` mappings. The host audit and mapping
creation may still cover all eight physical GPUs; only the four configured
mapping names are attached to this VM.

The normal repository variables are also required (`TF_VAR_proxmox_api_token`,
`TF_VAR_root_password`, `TF_VAR_ssh_public_keys`, `TF_VAR_netbox_token`). Run
OpenTofu from `services/llm/terraform`; the service is now pipeline-managed, so
changes to this configuration generate the normal validate/plan/approve/apply
jobs. Inspect the plan, then approve it. The plan must show Q35, OVMF, NUMA
node 0, VirtIO display and exactly four `hostpci` blocks. It must not delete an
existing VM or disk.

## 4. Install the guest and vLLM

After the VM has booted and SSH/QEMU guest agent work, select the four GPUs
from the guest's real topology:

```bash
python3 services/llm/scripts/select-gpus.py
export LLM_MODEL_GPU_UUIDS='GPU-...,GPU-...,GPU-...,GPU-...'
ANSIBLE_LIMIT=llm-main ansible-playbook services/llm/ansible/llm.yml
```

Ansible automatically runs `services/llm/scripts/select-gpus.py` on the guest
when `LLM_MODEL_GPU_UUIDS` is unset and `nvidia-smi` is available. For the
managed GitLab pipeline, you can instead provide the selected value as the
project or group CI/CD variable `LLM_MODEL_GPU_UUIDS`. It must contain exactly
four unique UUIDs selected from the four GPUs in the guest topology. Ensure
the variable is available to the pipeline type being run; protected variables
are not exposed to unprotected merge request pipelines.

The playbook installs Debian 13 compute prerequisites and NVIDIA's headless
driver from the official Debian 13 CUDA repository (an optional branch pin can
be supplied in `config.yaml` after compatibility review), then stops with an
explicit reboot instruction if the driver is not active. After reboot, rerun
the playbook with the same UUIDs. It creates `/opt/llm/venvs/vllm`, installs
Python 3.12 through `uv`, installs vLLM 0.26.0, downloads the model into
`/srv/llm/huggingface`, and enables `vllm-main.service`.

Because large Python wheels may time out through the egress proxy, the current
configuration enables `vllm.emergency_pip`. In that mode pip first writes a
dependency report, then Ansible downloads every artifact into
`/srv/llm/wheels` with resumable curl transfers and installs only from that
local wheelhouse. A failed pipeline run can be rerun safely; completed wheels
are hash-checked and partial wheels resume from their existing size.

The playbook waits for `/health`, then checks `/v1/models`, chat completions,
coding output, a multi-step reasoning response, tool calling and that only the
selected four GPUs hold model memory. Runtime success is never claimed by this
repository without actually running these commands on the real VM.

## 5. Validate long context and baseline performance

Run manually after the model is healthy:

```bash
llm-context-test       # 64K, then 96K, then 128K; writes JSON to /srv/llm/logs
llm-benchmark          # service active timestamp, TTFT and tokens/sec baseline
```

If 128K fails, preserve the error and GPU memory report before applying the
documented fallback order; do not silently change the final context target.

## References

- [NVIDIA Debian CUDA installation guide](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)
- [vLLM engine arguments](https://docs.vllm.ai/en/latest/serving/engine_args.html)
- [Qwen3.8 GPTQ checkpoint](https://huggingface.co/sokada4/Qwen3.8-27B-GPTQ-Int4)
- [Proxmox provider VM resource](https://github.com/bpg/terraform-provider-proxmox/blob/main/docs/resources/virtual_environment_vm.md)
