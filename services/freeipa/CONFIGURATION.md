# FreeIPA configuration reference

Каталог `services/freeipa` содержит декларативное описание размещения FreeIPA,
identity-объектов и PKI. Ansible применяет эти файлы, но не генерирует их.

## Структура

```text
services/freeipa/
├── config.yaml                 # сервис, VM, DNS, FreeIPA и OpenBao
├── README.md                   # порядок развёртывания и миграции
├── CONFIGURATION.md            # этот справочник
├── ansible/
│   ├── freeipa.yml             # полный apply на реплики
│   └── freeipa-objects.yml     # identity/PKI на primary
├── identity/
│   ├── README.md
│   ├── users.yaml              # обычные пользователи
│   ├── service_users.yaml      # сервисные пользователи
│   ├── groups.yaml             # группы
│   ├── hosts.yaml              # host entries
│   ├── services.yaml           # Kerberos principals
│   ├── hbac.yaml               # HBAC rules
│   └── sudo.yaml               # sudo rules
├── pki/
│   ├── profiles/*.cfg          # дополнительные Dogtag profiles
│   └── trust/freeipa-ca.crt    # публичный Root CA
└── terraform/                  # VM/Proxmox/NetBox wiring
```

Private keys, keytabs и пароли в Git не хранятся.

## `config.yaml`

### Сервис, VM и сеть

```yaml
service:
  name: freeipa
  topology: replicated
  description: FreeIPA identity-management service.
  tags: [opentofu, freeipa, identity]

placement:
  clusters:
    example-cluster:
      nodes:
        server:
          replicas_by_contour: {A: 1, B: 1}

rollout:
  by_contour: true

instance:
  vm_id_ranges:
    example-cluster:
      A: {start: 1030}
      B: {start: 1040}

compute:
  cpu_cores: 4
  memory_mb: 4096
  disk_gb: 20

network:
  interfaces:
    - name: net0
      network: app
      mode: netbox
      role: identity
      dns_name_pattern: freeipa-%{cluster}-%{contour}-%02d
      ansible_primary: true
```

`replicas_by_contour` задаёт количество VM. `ansible_primary` означает, что
адрес берётся из `ansible_host` инвентаря NetBox.

### Внешний DNS и маршрутизация

```yaml
dns:
  canonical_name: ipa.example.test
  zone: example.test
  ttl: 60
  records:
    - name: _ldap._tcp
      type: SRV
      value: 0 100 389 freeipa-node-a.example.test
    - name: _kerberos
      type: TXT
      value: EXAMPLE.TEST
  routing:
    mode: ingress
    ingress_service: traefik
```

SRV/TXT хранятся здесь. Для SRV в конфигурации используется обычная запись
из четырёх полей `priority weight port target`. При синхронизации priority
передаётся в отдельное поле NetBox, а `value` сохраняется как `weight port
target`; это формат, который ожидают NetBox DNS и PowerDNS API. A-записи VM и `ipa.example.test` создаёт внешний DNS
pipeline. Встроенный DNS FreeIPA отключён через `setup_dns: false`.

### FreeIPA

```yaml
freeipa:
  public_hostname: ipa.example.test
  domain: example.test
  realm: EXAMPLE.TEST
  setup_dns: false
  setup_ca: true
  reset_data: false
  replica_source_host: freeipa-node-a.example.test
  ports:
    http: 80
    https: 443
    ldap: 389
    ldaps: 636
    kerberos: 88
    kpasswd: 464
```

`reset_data: false` не менять: удаление базы FreeIPA выполняется только
отдельной процедурой с backup.

### IaC и healthcheck

```yaml
  iac:
    root: /var/lib/freeipa/iac
    profiles_dir: /var/lib/freeipa/iac/profiles
    identity_dir: /var/lib/freeipa/iac/identity
    backup_dir: /var/lib/freeipa/iac/backups
    healthcheck_timeout_seconds: 60
    external_dns_validation: true
```

`ipahealthcheck.ipa.idns` не используется как failure gate, поскольку DNS
внешний. Проверяются сервисы FreeIPA, Dogtag, LDAP, репликация, topology и
сертификаты, а A/SRV/TXT проверяются отдельно.

### OpenBao

```yaml
secrets:
  path: kv/services/freeipa
  fields:
    - freeipa_admin_password
    - freeipa_directory_manager_password
    - keycloak_freeipa_bind_password
```

Pipeline экспортирует поля как `FREEIPA_ADMIN_PASSWORD`,
`FREEIPA_DIRECTORY_MANAGER_PASSWORD` и `KEYCLOAK_FREEIPA_BIND_PASSWORD`.
Новый service account добавляется одновременно в `service_users.yaml` и
`secrets.fields`.

## Identity-файлы

Каждый YAML-файл содержит корневой ключ своего типа. Отсутствие объекта в Git
не удаляет его из FreeIPA. Удаление только явно через `state: absent`; перед
таким изменением роль создаёт online backup.

### `users.yaml` — обычные пользователи

```yaml
users:
  - name: ivan.petrov
    state: present
    givenname: Ivan
    sn: Petrov
    password_env: IVAN_FREEIPA_PASSWORD
    password_expiration: 20380119031407Z
```

### `service_users.yaml` — сервисные пользователи

```yaml
users:
  - name: monitoring-bind
    state: present
    givenname: Monitoring
    sn: Bind
    password_env: MONITORING_FREEIPA_BIND_PASSWORD
    password_expiration: 20380119031407Z
```

Пароль берётся из OpenBao через environment variable, но не записывается в
YAML. Пример для удаления:

```yaml
users:
  - name: old-bind
    state: absent
```

### `groups.yaml`

```yaml
groups:
  - name: monitoring-readers
    state: present
    description: Read-only monitoring accounts
```

### `hosts.yaml`

```yaml
hosts:
  - name: monitor.example.test
    state: present
    force: false
```

### `services.yaml` — Kerberos principals

```yaml
services:
  - name: HTTP/monitor.example.test@EXAMPLE.TEST
    state: present
    force: false
```

`services.yaml` — это Kerberos principal, а `service_users.yaml` — LDAP-user.

### `hbac.yaml`

```yaml
hbac_rules:
  - name: allow-monitoring
    state: present
    description: Allow monitoring group to monitoring hosts
    usergroup: monitoring-readers
    hostgroup: monitoring
    service: sshd
```

Поля: `name`, `state`, `description`, `host`, `hostgroup`, `user`,
`usergroup`, `service`.

### `sudo.yaml`

```yaml
sudo_rules:
  - name: monitoring-restart-agent
    state: present
    description: Restart monitoring agent
    usergroup: monitoring-readers
    hostgroup: monitoring
    command: /usr/bin/systemctl restart monitoring-agent
```

Поля: `name`, `state`, `description`, `user`, `usergroup`, `host`,
`hostgroup`, `command`.

## PKI-файлы

### `pki/profiles/*.cfg`

Дополнительные Dogtag profiles добавляются как файлы `<profile-name>.cfg`.
Ansible импортирует профиль только если его ещё нет. Встроенные профили не
изменяются и не удаляются.

### `pki/trust/freeipa-ca.crt`

Публичный Root CA FreeIPA в PEM-формате. Playbook копирует его на Traefik в
`/opt/traefik/config/freeipa-ca.crt`, чтобы Traefik проверял HTTPS backend
FreeIPA без `insecureSkipVerify`. Private key рядом отсутствует.

## Ansible-файлы

### `ansible/freeipa.yml`

Основной playbook. Он применяет установку/настройку FreeIPA, identity-файлы,
targeted healthchecks, внешние DNS acceptance checks и копирует Root CA на
Traefik. Реплики обрабатываются последовательно (`serial: 1`).

### `ansible/freeipa-objects.yml`

Узкий playbook для повторного применения identity и PKI на primary. Он удобен
для изменения пользователей, групп, HBAC, sudo rules и Dogtag profiles без
запуска VM/bootstrap-части.

Пароли playbook получает только из environment:

```text
FREEIPA_ADMIN_PASSWORD
FREEIPA_DIRECTORY_MANAGER_PASSWORD
KEYCLOAK_FREEIPA_BIND_PASSWORD
```

### `traefik.yml.j2`

Описание HTTP ingress FreeIPA:

```yaml
http:
  - name: freeipa
    host: ipa.example.test
    service: freeipa-svc
    backend_port: 443
    backend_scheme: https
    servers_transport: freeipa-tls
    pass_host_header: true
    health_check:
      path: /ipa/ui/
      scheme: https
```

Traefik завершает внешний HTTPS через Let’s Encrypt, а до FreeIPA подключается
по HTTPS на 443. `freeipa-tls` использует Root CA FreeIPA и `serverName:
ipa-ca.example.test`; отключение проверки сертификата запрещено.

LDAP, LDAPS, Kerberos и kpasswd через этот ingress не проксируются. Их клиенты
используют прямые DNS/SRV-записи.

## Terraform-файлы

Terraform создаёт только VM и сетевую регистрацию. Политики FreeIPA и identity
объекты Terraform не управляет.

### `terraform/main.tf`

Подключает общий `service_config`, читает `platform.yaml` и создаёт VM через
`proxmox_cloudinit_vm` для каждой рассчитанной экземплярной записи.

### `terraform/variables.tf`

```hcl
variable "deployment_cluster" {
  type    = string
  default = "example-cluster"
}

variable "deployment_contour" {
  type    = string
  default = "A"
}
```

Также принимает чувствительные `proxmox_api_token`, `netbox_token`,
`root_password` и `ssh_public_keys`. Их значения не коммитятся.

### `terraform/providers.tf`

Настраивает Proxmox provider из параметров выбранного кластера. Endpoint и
режим проверки TLS берутся из `environments/example/platform.yaml`.

### `terraform/versions.tf`

Фиксирует backend PostgreSQL и версии providers `bpg/proxmox` и
`hashicorp/external`.

## Применение

Полный apply:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/freeipa/ansible/freeipa.yml
```

Только identity/PKI на primary:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/freeipa/ansible/freeipa-objects.yml
```

Проверка без изменений:

```sh
ansible-playbook -i shared/ansible/inventory/netbox.yml \
  services/freeipa/ansible/freeipa.yml --check --diff
```
