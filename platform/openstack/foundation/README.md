# OpenStack foundation

Этот каталог содержит человекочитаемое описание базовых ресурсов OpenStack.
YAML-файлы являются источником конфигурации. Все Terraform-файлы foundation
roots генерируются из YAML через Jinja и не редактируются вручную.

## Структура

```text
foundation/
├── provider.yaml          # настройки провайдера
├── projects/              # настройки проектов
│   └── services/
│       ├── identity.yaml
│       ├── network.yaml
│       ├── security.yaml
│       ├── compute-catalog.yaml
│       ├── images.yaml
│       └── <generated Terraform roots>/
├── templates/             # Jinja-шаблоны всех Terraform-файлов
└── ...
```

Генератор размещает Terraform-файлы рядом с настройками проекта:
`projects/<project>/<component>/`. Эти файлы генерируются в CI и не хранятся в
Git.

`projects/<project>/identity.yaml` содержит имя проекта и его квоты. Остальные
YAML-файлы находятся в том же каталоге. Добавление проекта требует создать
каталог с `identity.yaml`. При нескольких
проектах генератор выбирает проект через `OPENSTACK_FOUNDATION_PROJECT`.

## Что и где писать

### `provider.yaml`

Описывает контракт OpenStack provider:

```yaml
provider:
  cloud_variable: openstack_cloud
  auth_url_variable: openstack_auth_url
  region_variable: openstack_region
  version: "~> 3.0"
```

Credentials сюда не записываются. Provider получает их из OpenStack environment
или из `clouds.yaml`.

### `projects/<project>/identity.yaml`

Здесь описываются настройки проекта, которому принадлежит каталог:

```yaml
identity:
  description: DRG service workloads
  compute_quota:
    cores: 256
    instances: 100
    ram: 524288
    key_pairs: 100
    server_groups: 50
    server_group_members: 200
  network_quota:
    network: 100
    subnet: 100
    port: 500
    router: 50
    floatingip: 100
    security_group: 50
    security_group_rule: 500
  volume_quota:
    volumes: 100
    snapshots: 100
    gigabytes: 10000
    backups: 50
    backup_gigabytes: 10000
```

Имя каталога (`services`) становится именем проекта. Квоты задаются явно для
Nova, Neutron и Cinder. Foundation security groups, сети и подсети создаются
в этом же проекте, а не в проекте `admin`.

### `projects/<project>/network.yaml`

Описывает существующую provider network, tenant network и router:

```yaml
network:
  external:
    name: provider-external
    physical_network: physnet1
    network_type: flat
    mtu: 1500
    shared: true
  tenant:
    name: services
    cidr: 203.0.113.0/24
    gateway_ip: 203.0.113.1
    mtu: 1440
    dns_nameservers: []
    router_name: services-router
    tenant_project_id_variable: tenant_project_id
```

Перед изменением сети нужно проверить CIDR, gateway, MTU и physical network.
Provider network создаётся Kolla/Neutron и находится Terraform через data source;
foundation не пытается создать её повторно на том же `physical_network`.
Нельзя использовать management-сеть `192.0.2.0/24` как tenant CIDR.

### `projects/<project>/security.yaml`

Содержит security groups и входящие правила:

```yaml
security:
  management_cidr: 192.0.2.0/24
  groups:
    sg-example:
      description: Example service
      rules:
        https:
          protocol: tcp
          port_range_min: 443
          port_range_max: 443
          remote_group_name: sg-traefik
```

Доступ можно ограничить CIDR:

```yaml
remote_ip_prefix: 192.0.2.0/24
```

или другой security group:

```yaml
remote_group_name: sg-traefik
```

`remote_group_name` должен ссылаться на группу, объявленную в этом же
`security.yaml`. Генератор передаст имя в модуль, а модуль разрешит его в
Neutron group ID. UUID вручную писать не нужно.

Не использовать широкие правила вроде `0.0.0.0/0`, если они не нужны
осознанно. Новые service groups следует добавлять с минимальным набором
портов и явно описывать направление доступа.

### `projects/<project>/compute-catalog.yaml`

Содержит flavors:

```yaml
compute_catalog:
  flavors:
    infra-small:
      vcpus: 1
      ram_mb: 2048
      disk_gb: 20
      description: Small infrastructure workload
```

`ram` и `disk` задаются с единицами размера: `MB`, `GB` или `TB`.
Например:

```yaml
ram: 4GB
disk: 40GB
```

Для RAM генератор переводит значение в MB, а для диска — в GB, как этого
требует OpenStack. Для диска значение должно целиком конвертироваться в GB.
Не создавать flavor для каждого отдельного VM без необходимости.

### `projects/<project>/images.yaml`

Пока загрузка образов отключена пустой картой:

```yaml
images: {}
```

Для добавления образа нужны URL и заранее проверенный checksum:

```yaml
images:
  debian-13:
    source_url: https://example.invalid/debian-13.qcow2
    checksum: <sha256>
    checksum_algo: sha256
    disk_format: qcow2
    container_format: bare
    visibility: private
    min_disk_gb: 10
    min_ram_mb: 1024
    properties:
      hw_machine_type: q35
```

В образах не хранить пароли, OpenBao tokens, IPA secrets, сертификаты и
конфигурацию конкретного сервиса.

## Рабочий процесс

1. Изменить нужный YAML-файл.
2. При необходимости сгенерировать Terraform в рабочем каталоге:

   ```bash
   uv run --with pyyaml --with jinja2 \
     python platform/openstack/scripts/generate-foundation.py
   ```

   В CI этот шаг выполняется автоматически перед `validate`, `plan` и
   `apply`. Сгенерированные `*.tf` не хранятся в репозитории.

3. Проверить воспроизводимость уже существующей локальной генерации:

   ```bash
   uv run --with pyyaml --with jinja2 \
     python platform/openstack/scripts/generate-foundation.py --check
   ```

4. Проверить нужный root:

   ```bash
   cd platform/openstack/foundation/projects/services/security
   tofu init -backend=false
   tofu validate
   tofu plan
   ```

`tofu plan` только показывает изменения. `tofu apply` создаёт реальные
ресурсы и выполняется отдельно после проверки плана.

## Что не редактировать вручную

Не редактировать напрямую:

- `*.tf` в foundation roots — это временные generated-файлы;
- `.terraform/`;
- `*.tfstate`;
- `*.tfvars` с credentials;
- `.terraform.lock.hcl` после успешного `tofu init`, кроме осознанного обновления provider.

Provider credentials передаются через environment или `clouds.yaml`, например
через `OS_CLOUD` либо `TF_VAR_openstack_cloud`. Секреты в YAML и Git не
сохранять.

Существующие `services/*` roots, Proxmox-модули и CI пока используют старую
модель и этим каталогом не управляются.
