# Architecture

The physical host remains headless. Its display adapter, if any, is not used
by Proxmox or by the guest. The Debian VM keeps a VirtIO virtual display and
QEMU guest agent for console and lifecycle operations, while four explicitly
selected RTX 3080 devices are passed through as PCIe mappings.
Host VFIO preparation may still audit all eight physical devices.

The VM is Q35/OVMF, CPU type `host`, NUMA enabled, VirtIO network and VirtIO
SCSI storage. It has 8 vCPUs and 64 GiB memory bound to host NUMA node 0. vLLM
is also launched with `numactl` bound to guest NUMA node 0. The four passed-through
PCI devices are selected at runtime by stable GPU UUID.

vLLM uses the local multiprocessing executor with `PP=4` and `TP=1`.
Pipeline parallelism is the baseline because these consumer cards do not have
NVLink; the selector still records PIX/PXB/PHB/SYS topology and NUMA affinity
so the chosen four can be reviewed before deployment. Qwen3.8 has 64 language
model layers and a native 262,144-token context; this service targets 131,072
tokens without RoPE/YaRN changes.

The checkpoint is text-only for this deployment even though the base model
contains a vision tower. `--language-model-only` prevents accidental vision
memory use. The model's hybrid attention/recurrent cache remains at the
checkpoint/vLLM automatic precision (`--mamba-cache-dtype auto` and
`--mamba-ssm-cache-dtype auto`).

The API listens on the VM application network at `/v1`. It is not exposed to
the Internet by this change; the deployment must restrict port 8000 at the
existing network/firewall boundary.

The launcher always requires the Qwen reasoning parser. It adds
`qwen3_xml` tool-calling flags only when the installed vLLM help output exposes
both options and that parser name, so tool support cannot block the baseline
text service.
