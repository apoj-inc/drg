# Rollback

## VFIO/boot configuration

Use the exact backup printed by `configure-vfio.sh apply`:

```bash
services/llm/scripts/rollback-vfio.sh /var/lib/llm-vfio-backup/TIMESTAMP
reboot
```

The command restores `/etc/default/grub` or `/etc/kernel/cmdline`, the
dedicated modules and modprobe files, rebuilds initramfs and refreshes the
same boot mechanism. Never remove a VM or disk as part of VFIO rollback.

## VM configuration

Stop the guest through Proxmox, save the Terraform plan/state, and revert the
reviewed module inputs for mappings/NUMA/firmware. Apply only after confirming
that the plan changes the intended VM ID. If the VM is managed by HA, follow
the repository's normal HA procedure; do not force-delete its disks.

## Guest driver and vLLM

Stop `vllm-main`, preserve `journalctl -u vllm-main -b` and the benchmark
artifacts, then remove only the packages or environment introduced by this
service using the package manager and the documented paths. Keep `/srv/llm`
until model/cache evidence has been archived. Reinstalling the previous
driver or vLLM environment is preferable to deleting it during incident
response.
