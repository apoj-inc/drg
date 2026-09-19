# Network service

This service reconciles Proxmox SDN from live NetBox data.

NetBox is the source of truth. The NetBox Ansible `default_sdn.yml` file only seeds initial objects; Terraform reads current NetBox VRF, L2VPN, prefix, IP address, route target, and config context data on every plan/apply.

## Workflow

```powershell
cd services/network/terraform
tofu init
tofu plan
tofu apply
```

After changing the bootstrap model in `services/netbox/ansible/config/default_sdn.yml`, run the NetBox Ansible playbook first so the NetBox objects and custom fields are created or updated. After changing objects directly in NetBox UI/API, run only `tofu plan/apply`.

## Files

- `services/netbox/ansible/config/default_sdn.yml` seeds the initial SDN inventory in NetBox.
- `services/netbox/ansible/netbox.yml` creates NetBox custom fields, tags, VRFs, L2VPNs, prefixes, IPs, route targets, and the config context.
- `services/network/terraform/scripts/netbox_sdn.py` reads live NetBox API data and normalizes it for Terraform.
- `services/network/terraform/main.tf` creates Proxmox SDN fabric, EVPN controller, zones, VNets, subnets, and applies SDN changes.

## NetBox Contract

Terraform discovers only objects tagged with:

- `proxmox-sdn`
- `terraform-managed`
- `proxmox-evpn` for overlay objects
- `proxmox-underlay` for fabric objects

The config context `proxmox-sdn-terraform-selectors` stores selectors and defaults, not a static copy of topology. Topology changes should be made in NetBox core objects.

The extractor script uses only Python stdlib HTTP so Terraform plans do not depend on extra Python packages.

## Object Mapping

| Proxmox SDN concept | NetBox object | Required tags | Important custom fields |
| --- | --- | --- | --- |
| OpenFabric fabric prefix | IPAM Prefix | `proxmox-sdn`, `proxmox-underlay`, `terraform-managed` | `proxmox_underlay_protocol`, `proxmox_underlay_role=fabric` |
| Node loopback/router identity | IPAM IP Address | `proxmox-sdn`, `proxmox-underlay`, `terraform-managed` | `proxmox_node`, `proxmox_sdn_id`, `proxmox_underlay_role=loopback` |
| Node OpenFabric IP | IPAM IP Address | `proxmox-sdn`, `proxmox-underlay`, `terraform-managed` | `proxmox_node`, `proxmox_sdn_id`, `proxmox_underlay_role=fabric`, `proxmox_fabric_interfaces` |
| EVPN zone | IPAM VRF | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | `proxmox_sdn_id`, `proxmox_sdn_zone`, `proxmox_sdn_controller`, `proxmox_vrf_vni`, exit node fields |
| EVPN route target | IPAM Route Target | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | zone/VNet metadata |
| VNet overlay | VPN L2VPN | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | `proxmox_sdn_id`, `proxmox_vnet`, `proxmox_vni`, `proxmox_sdn_zone` |
| Optional local VLAN tag | IPAM VLAN | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | `proxmox_sdn_id`, `proxmox_vnet`, `proxmox_vni`, `proxmox_sdn_zone` |
| VNet subnet | IPAM Prefix | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | `proxmox_vnet`, `proxmox_vni`, `proxmox_snat`, `proxmox_sdn_zone` |
| VNet gateway | IPAM IP Address | `proxmox-sdn`, `proxmox-evpn`, `terraform-managed` | `proxmox_vnet`, `proxmox_vni`, `proxmox_sdn_zone` |

## Config Context

NetBox object: `proxmox-sdn-terraform-selectors`

Path in UI: `Customization -> Config Contexts`.

It contains global selectors and defaults:

- Terraform object tags.
- Fabric ID/name/protocol/MTU.
- Controller defaults like ASN.
- Object type hints for zones, VNets, subnets, and gateways.

Do not put the full topology there. Terraform should discover live VRFs, L2VPNs, prefixes, and IPs from NetBox.

## Adding A Proxmox Node

Create or update these NetBox objects:

1. Add the node loopback IP in `IPAM -> IP Addresses`.
   - Example: `203.0.113.4/32`
   - Tags: `proxmox-sdn`, `proxmox-underlay`, `terraform-managed`
   - Custom fields:
     - `proxmox_node`: `pve4`
     - `proxmox_sdn_id`: `pve4`
     - `proxmox_underlay_protocol`: `openfabric`
     - `proxmox_underlay_role`: `loopback`

2. Add the node fabric IP in `IPAM -> IP Addresses`.
   - Example: `198.51.100.4/24`
   - It must be inside the fabric prefix, currently `198.51.100.0/24`.
   - Tags: `proxmox-sdn`, `proxmox-underlay`, `terraform-managed`
   - Custom fields:
     - `proxmox_node`: `pve4`
     - `proxmox_sdn_id`: `pve4`
     - `proxmox_underlay_protocol`: `openfabric`
     - `proxmox_underlay_role`: `fabric`
     - `proxmox_fabric_interfaces`: comma-separated interface list, for example `eno1`

3. Ensure EVPN zones include the new node if it should host that zone.
   - Current Terraform model deploys each zone to all discovered SDN nodes.
   - If per-zone node membership is needed later, add a dedicated VRF custom field and update the extractor.

4. Run `tofu plan/apply`.

If the node is only added to `default_sdn.yml`, run the NetBox Ansible playbook before Terraform.

## Adding An EVPN Zone

In NetBox, create:

1. A Route Target in `IPAM -> Route Targets`.
   - Example: `65000:30000`
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: short Proxmox zone ID, max 8 chars, for example `lab`
     - `proxmox_sdn_zone`: display zone name, for example `lab`
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`

2. A VRF in `IPAM -> VRFs`.
   - Name: for example `vrf-lab`
   - Import/export route target: the zone route target, for example `65000:30000`
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: `lab`
     - `proxmox_sdn_zone`: `lab`
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`
     - `proxmox_vrf_vni`: unique VRF VNI, for example `30000`
     - `proxmox_exitnodes`: comma-separated node IDs, for example `pve1`
     - `proxmox_primary_exitnode`: for example `pve1`
     - `proxmox_exitnodes_local_routing`: `true` or `false`

3. Optionally create a VLAN group for local VLAN attachments.

4. Add at least one VNet and subnet.

5. Run `tofu plan/apply`.

Proxmox SDN IDs should be short and stable. Keep display names friendly, but keep `proxmox_sdn_id` suitable for Proxmox.

## Adding A VNet

In NetBox, create:

1. A VNet Route Target in `IPAM -> Route Targets`.
   - Example: `65000:10300`
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: VNet ID, for example `labapp`
     - `proxmox_sdn_zone`: zone name, for example `lab`
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`
     - `proxmox_vnet`: VNet display name, for example `lab-app`
     - `proxmox_vni`: VNet VNI, for example `10300`

2. An L2VPN in `VPN -> L2VPNs`.
   - Name: for example `lab-app`
   - Type: `vxlan`
   - Identifier: VNI, for example `10300`
   - Import/export route target: VNet route target, for example `65000:10300`
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: `labapp`
     - `proxmox_sdn_zone`: `lab`
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`
     - `proxmox_vnet`: `lab-app`
     - `proxmox_vni`: `10300`
     - `proxmox_vrf_vni`: zone VRF VNI, for example `30000`

3. Optionally create a VLAN in `IPAM -> VLANs`.
   - This is local attachment/tag metadata.
   - Use the same tags and VNet custom fields.

4. Add a subnet prefix and gateway IP.

5. Run `tofu plan/apply`.

## Adding A Subnet

For a subnet inside an existing VNet:

1. Create a Prefix in `IPAM -> Prefixes`.
   - Example: `10.130.0.0/24`
   - VRF: zone VRF, for example `vrf-lab`
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: VNet Proxmox ID, for example `labapp`
     - `proxmox_sdn_zone`: zone name, for example `lab`
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`
     - `proxmox_vnet`: VNet display name, for example `lab-app`
     - `proxmox_vni`: VNet VNI, for example `10300`
     - `proxmox_vrf_vni`: zone VRF VNI, for example `30000`
     - `proxmox_snat`: `true` or `false`

2. Create a Gateway IP in `IPAM -> IP Addresses`.
   - Example: `10.130.0.1/24`
   - VRF: same VRF as prefix.
   - Tags: `proxmox-sdn`, `proxmox-evpn`, `terraform-managed`
   - Custom fields:
     - `proxmox_sdn_id`: VNet Proxmox ID, for example `labapp`
     - `proxmox_sdn_zone`: zone name
     - `proxmox_sdn_zone_type`: `evpn`
     - `proxmox_sdn_controller`: `evpn-main`
     - `proxmox_vnet`: VNet display name
     - `proxmox_vni`: VNet VNI

3. Run `tofu plan/apply`.

The extractor matches subnet prefixes and gateway IPs by `proxmox_vnet`, so keep that value identical between the prefix, gateway IP, and L2VPN.

## Changing SNAT

SNAT lives on the NetBox Prefix custom field `proxmox_snat`.

Change it on the VNet subnet prefix:

- `true` means Terraform sets `snat = true` on `proxmox_sdn_subnet`.
- `false` means Terraform sets `snat = false`.

Then run `tofu plan/apply`.

## Changing Exit Nodes

Exit node settings live on the NetBox VRF custom fields:

- `proxmox_exitnodes`: comma-separated node IDs, for example `pve1,pve2`
- `proxmox_primary_exitnode`: one node ID, for example `pve1`
- `proxmox_exitnodes_local_routing`: `true` or `false`

Then run `tofu plan/apply`.

## Common Pitfalls

- Proxmox SDN IDs must be short and valid. Use `proxmox_sdn_id`; do not rely on display names.
- Fabric node IPs must be inside the fabric prefix. Current fabric prefix is `198.51.100.0/24`.
- Loopback IPs are identity/peer addresses and live in `203.0.113.0/24`.
- Terraform ignores objects missing the discovery tags.
- The NetBox config context is not the topology. VRFs, L2VPNs, prefixes, IPs, and route targets are the topology.
- If you changed `default_sdn.yml`, run the NetBox Ansible playbook first. If you changed NetBox UI/API directly, run Terraform only.
