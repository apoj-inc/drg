# Redis service

This service provides a three-node Redis HA cluster on unprivileged Debian LXC
containers:

- `redis-example-cluster-a-01`
- `redis-example-cluster-b-01`
- `redis-example-cluster-c-01`

Redis Sentinel elects the primary. Traefik connects directly to Redis on port
`6379` and uses an authenticated, role-only Redis health check, so replicas are
removed from the load-balancing pool and only the current primary receives
client connections through `redis.example.test:6379`.

Deploy the three contours in order through the generated pipeline, or run the
service Terraform root manually:

```sh
cd services/redis/terraform
tofu init
tofu plan
tofu apply
```

Configure the members after their Terraform states are applied:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/redis/ansible/redis.yml \
  --limit 'redis-example-cluster-a-01:redis-example-cluster-b-01:redis-example-cluster-c-01'
```

The password is read from `REDIS_PASSWORD` and stored at the service's
declared OpenBao path. NetBox uses this external endpoint after its A/B contour
deployment.

Changing an existing deployment from VMs to LXC changes the Terraform resource
type. Review the Terraform plan and ensure Redis data is backed up before
applying; the existing VM resources may be destroyed and recreated.
