# Troubleshooting

## IOMMU is not active

Check firmware VT-d/Above-4G/UEFI settings, then inspect `/proc/cmdline`,
`proxmox-boot-tool status` and `dmesg | grep -Ei 'DMAR|IOMMU|remapping'`.
Use the rollback directory before changing boot configuration again.

## A GPU is not bound to vfio-pci

Run `verify-vfio.sh`, `lspci -Dnnk` and inspect
`/etc/modprobe.d/llm-vfio.conf`. Confirm that every function's vendor/device
ID was collected from the live host. Rebuild initramfs and reboot only from a
maintenance window.

## The GPU is absent from the VM

Confirm the four configured Proxmox mapping names exist, the VM is on the
owning node, and the VM plan contains four `hostpci` blocks with `pcie = true` and
`xvga = false`. Keep the VirtIO display. Do not mark a physical GPU as the
primary display device.

## NVIDIA driver does not load

Inside Debian, check `uname -r`, matching cloud-kernel headers, `dkms status`,
`journalctl -k` and `nvidia-smi`. The guest playbook installs headers for the
active kernel and uses headless
`cuda-drivers` plus `nvidia-kernel-open-dkms`; it does not install an X
server. Reboot after a DKMS change and rerun preflight.

## vLLM fails to start or a worker crashes

Inspect `journalctl -u vllm-main -b`, run
`/opt/llm/venvs/vllm/bin/vllm serve --help`, and verify that the launcher still
uses `vllm serve --help=all` and exposes `--device-ids`,
`--pipeline-parallel-size`, `--reasoning-parser`, and the Qwen tool parser.
Confirm `nvidia-smi -L`, UUID order, NCCL diagnostics,
and `numactl -H`. Do not switch to tensor parallelism without a separately
reviewed benchmark.

## CUDA OOM or 128K does not fit

Capture per-GPU memory, vLLM logs, request token usage and the exact
configuration. First fix an incorrect GPU selection or stale process; then
consider a supported KV-cache dtype; then lower `max_num_batched_tokens`;
then test 98,304. 65,536 is diagnostic only. Do not add RoPE/YaRN scaling or
silently make the reduced context the final configuration.

## Model download fails

Check `/srv/llm` free space, proxy variables, CA certificates and access to
the Hugging Face model metadata. Resume the download as the `llm` user; do not
delete the cache as a first response.
