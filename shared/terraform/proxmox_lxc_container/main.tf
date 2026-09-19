locals {
  platform_config   = try(yamldecode(file("${path.root}/../../../environments/example/platform.yaml")), {})
  service_config    = try(yamldecode(file("${path.root}/../config.yaml")), {})
  ipa_client_config = try(local.platform_config.freeipa_client, {})

  egress_proxy_https   = try(trimspace(local.platform_config.egress_proxy.https), "")
  egress_proxy_enabled = local.egress_proxy_https != ""
  ipa_client_enabled = coalesce(
    var.ipa_client_enabled,
    try(local.ipa_client_config.enabled, false) && !contains(
      try(local.ipa_client_config.excluded_services, []),
      try(local.service_config.service.name, ""),
    ),
  )
  ipa_client_hostgroups = distinct(concat(
    ["managed-linux"],
    try(local.service_config.service.name, "") != "" ? ["service-${replace(local.service_config.service.name, "_", "-")}"] : [],
    try(local.service_config.freeipa_client.hostgroups, []),
  ))

  netbox_enabled     = var.netbox_enabled && var.netbox_url != null && var.netbox_site != null && var.netbox_cluster_name != null
  dns_netbox_enabled = length(var.dns_servers) == 0 && var.dns_netbox_hostname != null

  requested_network_interfaces = concat(
    var.network_interfaces,
    [],
  )

  network_interfaces = local.netbox_enabled ? jsondecode(data.external.netbox_prepare[0].result.interfaces) : local.requested_network_interfaces
  dns_servers = length(var.dns_servers) > 0 ? var.dns_servers : (
    local.dns_netbox_enabled ? [data.external.netbox_dns_server[0].result.ip] : []
  )
  ansible_primary_ip = try(split("/", one([
    for nic in local.network_interfaces : nic.address
    if try(nic.ansible_primary, false)
  ]))[0], null)
  ansible_primary_hostname = try(one([
    for nic in local.network_interfaces : nic.dns_name
    if try(nic.ansible_primary, false)
  ]), null)
}
data "external" "netbox_prepare" {
  count = local.netbox_enabled ? 1 : 0

  program = [var.python_command, "${path.module}/scripts/netbox_vm_ipam.py"]

  query = {
    action                     = "prepare"
    netbox_url                 = var.netbox_url
    netbox_token               = var.netbox_token
    netbox_validate_certs      = tostring(var.netbox_validate_certs)
    netbox_dns_records_enabled = tostring(var.netbox_dns_records_enabled)
    netbox_site                = var.netbox_site
    netbox_cluster_name        = var.netbox_cluster_name
    hostname                   = var.hostname
    node_name                  = var.node_name
    vm_id                      = tostring(var.vm_id)
    tags                       = jsonencode(var.tags)
    interfaces                 = jsonencode(local.requested_network_interfaces)
  }
}

data "external" "netbox_dns_server" {
  count = local.dns_netbox_enabled ? 1 : 0

  program = [var.python_command, "${path.module}/scripts/netbox_vm_primary_ip.py"]

  query = {
    netbox_url            = var.netbox_url
    netbox_token          = var.netbox_token
    netbox_validate_certs = tostring(var.netbox_validate_certs)
    netbox_cluster_name   = var.netbox_cluster_name
    hostname              = var.dns_netbox_hostname
  }
}

resource "proxmox_virtual_environment_container" "this" {
  node_name     = var.node_name
  vm_id         = var.vm_id
  description   = var.description
  unprivileged  = var.unprivileged
  started       = var.started
  start_on_boot = var.start_on_boot

  dynamic "startup" {
    for_each = var.startup_order == null ? [] : [1]

    content {
      order = var.startup_order
    }
  }

  features {
    nesting = var.feat_nesting
    keyctl  = var.feat_keyctl
    fuse    = var.feat_fuse
    mount   = var.feat_mount
  }

  initialization {
    hostname = var.hostname

    dynamic "dns" {
      for_each = length(local.dns_servers) > 0 || var.dns_domain != null ? [1] : []

      content {
        domain  = var.dns_domain
        servers = local.dns_servers
      }
    }

    dynamic "ip_config" {
      for_each = local.network_interfaces

      content {
        ipv4 {
          address = ip_config.value.address
          gateway = try(ip_config.value.gateway, null)
        }
      }
    }

    user_account {
      password = var.root_password
      keys     = var.ssh_public_keys
    }
  }

  wait_for_ip {
    ipv4 = true
  }

  cpu {
    cores = var.cpu_cores
  }

  memory {
    dedicated = var.memory_dedicated
    swap      = var.memory_swap
  }

  disk {
    datastore_id = var.disk_datastore_id
    size         = var.disk_size
  }

  dynamic "network_interface" {
    for_each = local.network_interfaces

    content {
      name        = network_interface.value.name
      bridge      = network_interface.value.bridge
      mtu         = try(network_interface.value.mtu, null)
      mac_address = try(network_interface.value.mac_address, null)
    }
  }

  operating_system {
    template_file_id = "${var.lxc_template_path}/${var.lxc_template_name}"
    type             = var.os_type
  }

  tags = var.tags
}

resource "terraform_data" "netbox_registration" {
  count = local.netbox_enabled ? 1 : 0

  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    sha256(jsonencode(local.network_interfaces)),
    sha256(jsonencode(var.tags)),
    tostring(var.netbox_dns_records_enabled),
    filesha256("${path.module}/scripts/netbox_vm_ipam.py"),
    filesha256("${path.module}/scripts/netbox_vm_primary_ip.py"),
  ]

  provisioner "local-exec" {
    command = "${var.python_command} ${path.module}/scripts/netbox_vm_ipam.py"

    environment = {
      NETBOX_VM_IPAM_QUERY = jsonencode({
        action                     = "finalize"
        netbox_url                 = var.netbox_url
        netbox_token               = var.netbox_token
        netbox_validate_certs      = tostring(var.netbox_validate_certs)
        netbox_dns_records_enabled = tostring(var.netbox_dns_records_enabled)
        netbox_site                = var.netbox_site
        netbox_cluster_name        = var.netbox_cluster_name
        hostname                   = var.hostname
        node_name                  = var.node_name
        vm_id                      = tostring(var.vm_id)
        tags                       = jsonencode(var.tags)
        interfaces                 = jsonencode(local.network_interfaces)
      })
    }
  }
}

# Kept separate from NetBox registration so that enrollment is only attempted
# after the hostname has a registered address and DNS record.
resource "terraform_data" "freeipa_client_enrollment" {
  count = local.ipa_client_enabled ? 1 : 0

  depends_on = [terraform_data.netbox_registration]

  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    local.ansible_primary_ip,
    local.ansible_primary_hostname,
    tostring(try(local.ipa_client_config.domain, "")),
    tostring(try(local.ipa_client_config.realm, "")),
    sha256(jsonencode(try(local.ipa_client_config.servers, []))),
  ]

  provisioner "local-exec" {
    command = <<-SHELL
      set -eu
      unset KRB5_CONFIG
      : "$${IPA_CLIENT_HOSTNAME:?}" "$${IPA_CLIENT_ADDRESS:?}" "$${IPA_CLIENT_DOMAIN:?}" "$${IPA_CLIENT_REALM:?}" "$${IPA_CLIENT_SERVERS:?}" "$${IPA_CLIENT_HOSTGROUPS:?}" "$${IPA_BOOTSTRAP_PRIVATE_KEY:?}" "$${IPA_BOOTSTRAP_PUBLIC_KEY:?}" "$${IPA_ENROLLER_PASSWORD:?}" "$${IPA_CA_CERT:?}"
      [ -r "$${IPA_BOOTSTRAP_PRIVATE_KEY}" ] && [ -r "$${IPA_CA_CERT}" ]
      runner_home=$${HOME}
      tmpdir=$(mktemp -d -t ipa-enroll.XXXXXX)
      trap 'kdestroy -A >/dev/null 2>&1 || true; rm -rf "$${tmpdir}"' EXIT
      export KRB5CCNAME="FILE:$${tmpdir}/krb5cc" HOME="$${tmpdir}" XDG_CACHE_HOME="$${tmpdir}/cache" IPA_CONFDIR="$${tmpdir}/ipa"
      primary_server=$${IPA_CLIENT_SERVERS%%,*}
      basedn=$(printf '%s' "$${IPA_CLIENT_DOMAIN}" | sed 's/[^.][^.]* */dc=&/g; s/\./,/g')
      mkdir -p "$${IPA_CONFDIR}" "$${XDG_CACHE_HOME}"
      cp "$${IPA_CA_CERT}" "$${IPA_CONFDIR}/ca.crt"
      printf '[global]\nbasedn = %s\ndomain = %s\nrealm = %s\nserver = %s\nxmlrpc_uri = https://%s/ipa/xml\njsonrpc_uri = https://%s/ipa/json\nin_server = False\ncontext = cli\ntls_ca_cert = %s\n' "$${basedn}" "$${IPA_CLIENT_DOMAIN}" "$${IPA_CLIENT_REALM}" "$${primary_server}" "$${primary_server}" "$${primary_server}" "$${IPA_CA_CERT}" >"$${IPA_CONFDIR}/default.conf"
      kdestroy -A >/dev/null 2>&1 || true
      printf '%s\n' "$${IPA_ENROLLER_PASSWORD}" | kinit -c "$${KRB5CCNAME}" "$${IPA_ENROLLER_PRINCIPAL:-ipa-enroller}"
      klist -c "$${KRB5CCNAME}"
      kvno "HTTP/$${primary_server}"
      host_add_payload=$(printf '{"method":"host_add","params":[["%s"],{"random":true,"force":true}],"id":0}' "$${IPA_CLIENT_HOSTNAME}")
      otp=''; last_api_error=''; old_ifs=$${IFS}; IFS=,
      for api_server in $${IPA_CLIENT_SERVERS}; do
        if host_add_response=$(curl --silent --show-error --http1.1 --negotiate --service-name HTTP -u : --cacert "$${IPA_CA_CERT}" -H 'Content-Type: application/json' -H 'Accept: application/json' -H "Referer: https://$${api_server}/ipa" --data "$${host_add_payload}" "https://$${api_server}/ipa/session/json"); then
          if printf '%s' "$${host_add_response}" | grep -Eq '"error"[[:space:]]*:[[:space:]]*null'; then otp=$(printf '%s' "$${host_add_response}" | sed -n 's/.*"randompassword"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'); fi
          if ! printf '%s' "$${host_add_response}" | grep -Eq '"error"[[:space:]]*:[[:space:]]*null'; then last_api_error=$(printf '%s' "$${host_add_response}" | sed -n 's/.*"message"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'); fi
          [ -n "$${otp}" ] && break
        fi
      done
      IFS=$${old_ifs}
      if [ -z "$${otp}" ]; then
        host_mod_payload=$(printf '{"method":"host_mod","params":[["%s"],{"random":true}],"id":0}' "$${IPA_CLIENT_HOSTNAME}")
        old_ifs=$${IFS}; IFS=,
        for api_server in $${IPA_CLIENT_SERVERS}; do
          if host_mod_response=$(curl --silent --show-error --http1.1 --negotiate --service-name HTTP -u : --cacert "$${IPA_CA_CERT}" -H 'Content-Type: application/json' -H 'Accept: application/json' -H "Referer: https://$${api_server}/ipa" --data "$${host_mod_payload}" "https://$${api_server}/ipa/session/json"); then
            if printf '%s' "$${host_mod_response}" | grep -Eq '"error"[[:space:]]*:[[:space:]]*null'; then otp=$(printf '%s' "$${host_mod_response}" | sed -n 's/.*"randompassword"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'); fi
            if ! printf '%s' "$${host_mod_response}" | grep -Eq '"error"[[:space:]]*:[[:space:]]*null'; then last_api_error=$(printf '%s' "$${host_mod_response}" | sed -n 's/.*"message"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'); fi
            [ -n "$${otp}" ] && break
          fi
        done
        IFS=$${old_ifs}
      fi
      [ -n "$${otp}" ] || { echo "ERROR: FreeIPA did not return a one-time enrollment password: $${last_api_error:-no API error message}" >&2; exit 1; }
      bash "${path.module}/../../../shared/terraform/scripts/reconcile_freeipa_hostgroups.sh"
      guest_script="$${tmpdir}/guest.sh"
      cat >"$${guest_script}" <<'GUEST'
      #!/bin/sh
      set -eu
      decode() { printf '%s' "$1" | base64 -d; }
      hostname=$(decode "$1"); domain=$(decode "$2"); realm=$(decode "$3"); servers=$(decode "$4"); otp=$(decode "$5"); bootstrap_public_key=$(decode "$6")
      export DEBIAN_FRONTEND=noninteractive
      if command -v apt-get >/dev/null 2>&1; then
        if [ "${local.egress_proxy_enabled}" = "true" ]; then
          printf '%s\n' \
            'Acquire::ForceIPv4 "true";' \
            'Acquire::https::Proxy "${local.egress_proxy_https}";' \
            >/etc/apt/apt.conf.d/80-egress-proxy
        else
          rm -f /etc/apt/apt.conf.d/80-egress-proxy
        fi
        apt-get update && apt-get install -y freeipa-client openssh-server sudo
      elif command -v dnf >/dev/null 2>&1; then dnf install -y freeipa-client openssh-server sudo
      elif command -v yum >/dev/null 2>&1; then yum install -y freeipa-client openssh-server sudo
      else echo 'No supported package manager for FreeIPA client bootstrap' >&2; exit 1; fi
      primary_server=$${servers%%,*}
      for retry in $(seq 1 3); do getent hosts "$${hostname}" >/dev/null 2>&1 && getent hosts "$${primary_server}" >/dev/null 2>&1 && break; [ "$${retry}" -eq 3 ] && { echo 'FreeIPA DNS is not ready' >&2; exit 1; }; sleep 20; done
      old_ifs=$${IFS}; IFS=,; set -- $${servers}; IFS=$${old_ifs}
      server_args=''; for server; do server_args="$${server_args} --server $${server}"; done
      # shellcheck disable=SC2086
      ipa-client-install --unattended --force-join --no-ntp --hostname "$${hostname}" --domain "$${domain}" --realm "$${realm}" $${server_args} --password "$${otp}" --mkhomedir
      install -d -m 0755 /etc/ssh/sshd_config.d
      printf '%s\n' 'GSSAPIAuthentication yes' 'GSSAPICleanupCredentials yes' >/etc/ssh/sshd_config.d/20-freeipa-gssapi.conf
      systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
GUEST
      b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
      ssh-keygen -f "$${runner_home}/.ssh/known_hosts" -R "$${IPA_CLIENT_ADDRESS}" >/dev/null 2>&1 || true
      for retry in $(seq 1 3); do
        if ssh -i "$${IPA_BOOTSTRAP_PRIVATE_KEY}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 "root@$${IPA_CLIENT_ADDRESS}" sh -s -- "$(b64 "$${IPA_CLIENT_HOSTNAME}")" "$(b64 "$${IPA_CLIENT_DOMAIN}")" "$(b64 "$${IPA_CLIENT_REALM}")" "$(b64 "$${IPA_CLIENT_SERVERS}")" "$(b64 "$${otp}")" "$(b64 "$${IPA_BOOTSTRAP_PUBLIC_KEY}")" <"$${guest_script}"; then break; fi
        [ "$${retry}" -eq 3 ] && { echo 'ERROR: cannot reach the new guest over bootstrap SSH.' >&2; exit 1; }; sleep 20
      done
    SHELL

    environment = {
      IPA_CLIENT_HOSTNAME   = "${coalesce(local.ansible_primary_hostname, var.hostname)}.${try(local.ipa_client_config.domain, "")}"
      IPA_CLIENT_ADDRESS    = local.ansible_primary_ip
      IPA_CLIENT_DOMAIN     = tostring(try(local.ipa_client_config.domain, ""))
      IPA_CLIENT_REALM      = tostring(try(local.ipa_client_config.realm, ""))
      IPA_CLIENT_SERVERS    = join(",", try(local.ipa_client_config.servers, []))
      IPA_CLIENT_HOSTGROUPS = join(",", local.ipa_client_hostgroups)
      # The CI bootstrap key is appended after the persistent Ansible key.
      IPA_BOOTSTRAP_PUBLIC_KEY = var.ssh_public_keys[length(var.ssh_public_keys) - 1]
      IPA_CA_CERT              = "${path.module}/../../../services/freeipa/pki/trust/freeipa-ca.crt"
    }
  }
}

resource "terraform_data" "freeipa_client_hostgroup_membership" {
  count = local.ipa_client_enabled ? 1 : 0

  depends_on = [terraform_data.freeipa_client_enrollment]

  triggers_replace = [
    proxmox_virtual_environment_container.this.id,
    local.ansible_primary_hostname,
    join(",", local.ipa_client_hostgroups),
  ]

  provisioner "local-exec" {
    command = "bash ${path.module}/../../../shared/terraform/scripts/reconcile_freeipa_hostgroups.sh"

    environment = {
      IPA_CLIENT_HOSTNAME   = "${coalesce(local.ansible_primary_hostname, var.hostname)}.${try(local.ipa_client_config.domain, "")}"
      IPA_CLIENT_SERVERS    = join(",", try(local.ipa_client_config.servers, []))
      IPA_CLIENT_HOSTGROUPS = join(",", local.ipa_client_hostgroups)
      IPA_CA_CERT           = "${path.module}/../../../services/freeipa/pki/trust/freeipa-ca.crt"
    }
  }
}
