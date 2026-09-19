# PR: add the first GPU-passthrough LLM deployment

## Summary

Adds an auditable Proxmox host preparation flow, a Debian 13 Q35/OVMF VM
definition with four PCI mappings, and a non-containerized vLLM service for
`sokada4/Qwen3.8-27B-GPTQ-Int4`.

## Architecture and decisions

- Debian 13 guest with VirtIO SCSI/network, virtual display and QEMU agent.
- NVIDIA driver is installed only in the guest; the Proxmox host uses VFIO.
- NVIDIA compute-only packages avoid desktop/display dependencies.
- Python 3.12 is isolated with `uv`; system Python is untouched.
- vLLM 0.26.0, GPTQ INT4, float16, `PP=4`, `TP=1`, and 131,072 target context.
- Qwen reasoning parser `qwen3` and model-card tool parser `qwen3_xml` are
  checked against `vllm serve --help` before launch.
- Four UUID-selected GPUs serve the model; four remain free.
- Host PCI IDs, addresses, NUMA placement, VM ID and sizing are recorded from a
  live audit in the deployment configuration; guest GPU UUIDs remain runtime
  inputs.
- Docker, RAG, vector databases, OCR, UI, authentication and monitoring are
  deliberately excluded.

## Validation

Static validation covers Terraform/OpenTofu formatting, YAML, Ansible role
presence and shell/Python syntax. The deployment playbook runs fail-fast
hardware/model preflight and API/GPU allocation smoke tests. Actual runtime
claims require an operator to run those tests on the physical server; this
workspace has no Proxmox/GPU credentials and therefore has not claimed them.

## Known limitations

Pipeline parallelism adds inter-GPU latency and the cards have no NVLink.
Each RTX 3080 has 10 GB VRAM. The baseline is single-user/low-concurrency;
128K may require the documented fallback investigation. Hardware mappings
require `root@pam`, and guest/host rollback requires a reboot. The first model
uses only four GPUs.

## Follow-up PRs

RAG, embeddings, reranking, vector storage, API gateway/authentication,
monitoring, and dedicated benchmarking are separate changes.
