# etcd service

Three-node etcd quorum used by PostgreSQL Patroni as its distributed configuration store.

The quorum is distributed across contours:

- `etcd-node-a`, container ID `1200`
- `etcd-node-b`, container ID `1204`
- `etcd-node-c`, container ID `1205`

Deploy etcd before PostgreSQL:

```sh
cd services/etcd/terraform
tofu init
tofu plan
tofu apply
```

Then configure it:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/etcd/ansible/etcd.yml \
  --limit 'etcd-node-a:etcd-node-b:etcd-node-c'
```
