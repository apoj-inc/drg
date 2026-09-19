# PowerDNS

PowerDNS authoritative DNS service backed by the shared PostgreSQL server. NetBox
remains the DNS source of truth; octoDNS computes and applies a reviewed diff
through the PowerDNS HTTP API.

## Terraform

```sh
cd services/powerdns/terraform
tofu init
tofu apply
```

CI/CD and local Ansible runs read the same values from environment variables.

## Ansible

Run from the repository root:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/powerdns/ansible/powerdns.yml --limit tags_powerdns
```

PowerDNS runs as one LXC in each of rollout contours A and B. dnsdist listens on
port 53 and balances authoritative `example.test` queries and recursive queries
across both PowerDNS instances. Use `198.51.100.100` for contour A and
`198.51.100.101` for contour B; reserve both addresses in NetBox before applying
either contour. Machines can use both addresses as recursive DNS servers, and
the canonical PowerDNS record publishes both healthy contour addresses.

## DNS sync

octoDNS runs only on the contour A PowerDNS node. Both authoritative replicas
read the same PostgreSQL backend, so a single API writer prevents races. The
Docker image is built from repository-owned files with pinned versions:
`octodns 1.22.0`, `octodns-netbox-dns 0.3.19`, and
`octodns-powerdns 1.2.0`.

DNS reconciliation is deliberately separate from the regular PowerDNS
deployment. The generated pipeline creates three jobs after PowerDNS has been
configured: `preview:powerdns-dns`, a manual `approve:powerdns-dns`, and
`apply:powerdns-dns`. Only the last job sends changes to the PowerDNS API, and
it always runs on the contour A controller.

The preview builds the pinned image, validates its configuration and prints the
octoDNS plan without `--doit`. It is intentionally not Ansible check-mode:
octoDNS must run to calculate the NetBox-to-PowerDNS diff.

The dedicated playbook remains available for an out-of-pipeline reconciliation:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/powerdns/ansible/sync_dns.yml
```

To print a non-mutating plan for diagnostics, explicitly disable the apply step:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml services/powerdns/ansible/sync_dns.yml -e pdns_sync_apply=false
```

After a successful reconciliation the legacy `netbox-pdns` Compose project is
stopped on the deployed node; `/opt/netbox-pdns` is retained for rollback. The
temporary PostgreSQL SRV trigger is removed by the regular PowerDNS playbook
because octoDNS sends SRV priority in PowerDNS's native API representation.
