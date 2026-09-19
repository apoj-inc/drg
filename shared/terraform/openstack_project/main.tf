resource "openstack_identity_project_v3" "this" {
  name        = var.name
  description = var.description
  domain_id   = var.domain_id
  enabled     = var.enabled
}

data "openstack_identity_user_v3" "admin" {
  name = "admin"
}

data "openstack_identity_role_v3" "admin" {
  name = "admin"
}

resource "openstack_identity_role_assignment_v3" "admin" {
  user_id    = data.openstack_identity_user_v3.admin.id
  project_id = openstack_identity_project_v3.this.id
  role_id    = data.openstack_identity_role_v3.admin.id
}

resource "openstack_identity_application_credential_v3" "this" {
  for_each = var.application_credentials

  name         = each.key
  description  = try(each.value.description, null)
  roles        = try(each.value.roles, [])
  unrestricted = try(each.value.unrestricted, false)
}

resource "openstack_compute_quotaset_v2" "this" {
  count = var.compute_quota == null ? 0 : 1

  project_id           = openstack_identity_project_v3.this.id
  cores                = try(var.compute_quota.cores, null)
  instances            = try(var.compute_quota.instances, null)
  ram                  = try(var.compute_quota.ram, null)
  key_pairs            = try(var.compute_quota.key_pairs, null)
  server_groups        = try(var.compute_quota.server_groups, null)
  server_group_members = try(var.compute_quota.server_group_members, null)
}

resource "openstack_networking_quota_v2" "this" {
  count = var.network_quota == null ? 0 : 1

  project_id          = openstack_identity_project_v3.this.id
  network             = try(var.network_quota.network, null)
  subnet              = try(var.network_quota.subnet, null)
  port                = try(var.network_quota.port, null)
  router              = try(var.network_quota.router, null)
  floatingip          = try(var.network_quota.floatingip, null)
  security_group      = try(var.network_quota.security_group, null)
  security_group_rule = try(var.network_quota.security_group_rule, null)
}

resource "openstack_blockstorage_quotaset_v3" "this" {
  count = var.volume_quota == null ? 0 : 1

  project_id       = openstack_identity_project_v3.this.id
  volumes          = try(var.volume_quota.volumes, null)
  snapshots        = try(var.volume_quota.snapshots, null)
  gigabytes        = try(var.volume_quota.gigabytes, null)
  backups          = try(var.volume_quota.backups, null)
  backup_gigabytes = try(var.volume_quota.backup_gigabytes, null)
}
