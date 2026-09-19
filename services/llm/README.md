# Local LLM service

This service describes one manually bootstrapped, single-node LLM deployment:

```text
Proxmox host -> Intel IOMMU/VFIO -> Debian 13 VM
             -> 4 x RTX 3080 passthrough -> NVIDIA driver
             -> Python 3.12/uv -> vLLM 0.26.0
             -> sokada4/Qwen3.8-27B-GPTQ-Int4, PP=4, TP=1
```

The VM receives four explicitly configured GPUs; the other physical GPUs remain
available for a future, separate service. Docker, RAG, embeddings,
reranking, OCR, gateways and monitoring are intentionally out of scope.

Read the documents in this order:

1. [Architecture](docs/architecture.md)
2. [Installation](docs/installation.md)
3. [Troubleshooting](docs/troubleshooting.md)
4. [Rollback](docs/rollback.md)
5. [PR description](docs/pr-description.md)

The host scripts are deliberately explicit:

```text
scripts/proxmox-audit.sh OUTPUT_DIR       # read-only
scripts/configure-vfio.sh check            # read-only
scripts/configure-vfio.sh apply            # writes boot/initramfs; backed up
scripts/verify-vfio.sh                     # read-only after reboot
scripts/create-pci-mappings.sh pve-node-a     # root@pam, cluster mappings
scripts/select-gpus.py                     # guest topology recommendation
```

The VM ID, sizing, NUMA placement and PCI mapping names are recorded in
`config.yaml` after the host audit. PCI addresses and guest GPU UUIDs remain
live hardware inputs, and credentials are supplied through the environment.

`pipeline.managed: true` includes this service in automatic CI plans when its
configuration or Terraform inputs change. Host VFIO preparation and PCI
mapping remain manual prerequisites; Terraform apply still waits for the
normal pipeline approval gate. The service uses contour pipeline stages while
keeping its singleton inventory hostname `llm-main` through
`pipeline.rollout_ansible_limit`.
