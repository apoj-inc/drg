# Локальный тест централизованного сбора логов

Этот стенд позволяет без секретов и подключения к инфраструктуре проверить
основной путь прохождения логов:

    тестовый Docker-контейнер
        → Grafana Alloy
        → Grafana Loki
        → Grafana Explore

Стенд не обращается к NetBox, OpenBao, PostgreSQL, OIDC и production-серверам.
SSH-ключи, API-токены и другие секреты не требуются.

## Состав стенда

Docker Compose запускает четыре контейнера:

| Сервис | Назначение | Локальный адрес |
|---|---|---|
| grafana | Интерфейс просмотра и поиска логов | http://localhost:3000 |
| loki | Хранение логов и выполнение LogQL-запросов | http://localhost:3100 |
| alloy | Обнаружение Docker-контейнеров и отправка логов в Loki | Не опубликован |
| log-generator | Генерация тестовой строки каждые две секунды | Не опубликован |

Данные Grafana, Loki и Alloy сохраняются в отдельных Docker volumes.

## Требования

Необходимы:

- запущенный Docker Desktop или Docker Engine;
- Docker Compose v2;
- свободные локальные порты 3000 и 3100.

Проверка:

    docker version
    docker compose version

Команда docker version должна вывести информацию и о Client, и о Server.

## Быстрый запуск

Все команды выполняются из корня репозитория:

    docker compose -f tests/logging/docker-compose.yml up -d

Первый запуск скачивает образы Grafana, Loki, Alloy и Alpine, поэтому может
занять несколько минут.

Проверить состояние:

    docker compose -f tests/logging/docker-compose.yml ps

Ожидается четыре запущенных контейнера:

    logging-test-grafana
    logging-test-loki
    logging-test-alloy
    logging-test-generator

Loki должен иметь состояние healthy.

## Проверка через Grafana

Откройте в браузере:

    http://localhost:3000

Для локального стенда включён анонимный доступ с правами администратора. Это
сделано только для теста и не относится к production-конфигурации.

В Grafana:

1. Откройте Explore.
2. Выберите источник данных Loki.
3. Выполните запрос:

       {service="log-generator"}

Должны появиться строки:

    local logging smoke test timestamp=... level=info

Полезные запросы:

Все локальные Docker-логи:

    {environment="local", source="docker"}

Логи конкретного контейнера:

    {container="logging-test-generator"}

Поиск тестового текста:

    {service="log-generator"} |= "local logging smoke test"

## Проверка через Loki API

Проверить готовность Loki:

    curl http://localhost:3100/ready

Ожидаемый ответ:

    ready

Получить тестовые записи:

    curl -G http://localhost:3100/loki/api/v1/query_range \
      --data-urlencode 'query={service="log-generator"}' \
      --data-urlencode 'limit=10'

В PowerShell кавычки в LogQL могут обрабатываться иначе. В таком случае
используйте URL-кодированный запрос:

    curl.exe -s "http://localhost:3100/loki/api/v1/query_range?query=%7Bservice%3D%22log-generator%22%7D&limit=10"

Успешный ответ содержит status со значением success и непустой массив result.

## Проверка конфигураций

Проверить Loki его собственным валидатором:

    docker compose -f tests/logging/docker-compose.yml exec loki \
      /usr/bin/loki -config.file=/etc/loki/loki.yml -verify-config

Проверить Alloy:

    docker compose -f tests/logging/docker-compose.yml exec alloy \
      /bin/alloy validate /etc/alloy/config.alloy

Обе команды должны завершиться с кодом 0.

## Просмотр служебных логов

Alloy:

    docker compose -f tests/logging/docker-compose.yml logs --tail 100 alloy

Loki:

    docker compose -f tests/logging/docker-compose.yml logs --tail 100 loki

Grafana:

    docker compose -f tests/logging/docker-compose.yml logs --tail 100 grafana

Генератор:

    docker compose -f tests/logging/docker-compose.yml logs --tail 20 log-generator

Следить за всеми контейнерами:

    docker compose -f tests/logging/docker-compose.yml logs -f

Выход из режима слежения: Ctrl+C. Контейнеры при этом продолжают работать.

## Что именно проверяет стенд

Стенд проверяет:

- запуск single-node Loki;
- корректность Loki schema и filesystem storage;
- автоматический provisioning Loki datasource в Grafana;
- запуск Alloy;
- доступ Alloy к Docker socket;
- обнаружение Docker-контейнеров;
- чтение stdout и stderr контейнеров;
- добавление labels;
- отправку записей из Alloy в Loki;
- поиск записей через LogQL и Grafana Explore;
- сохранение данных в Docker volumes.

Стенд не проверяет:

- NetBox dynamic inventory;
- подключение Ansible по SSH;
- сеть между настоящими LXC;
- production firewall;
- OpenBao и загрузку секретов;
- systemd journal настоящих сервисных хостов;
- отдельные файловые логи GitLab, FreeIPA и PostgreSQL;
- production retention и объём диска.

## Опциональный тест Ansible-роли в WSL

Docker-стенд проверяет движение данных. Дополнительно можно проверить, что
роль logging_agent действительно устанавливает и настраивает Alloy.

Этот тест предназначен для Ubuntu/Debian WSL с включённым systemd. Он изменяет
локальную WSL-систему:

- скачивает зафиксированный официальный пакет Alloy из GitHub Releases;
- проверяет SHA-256 и устанавливает пакет;
- создаёт /etc/alloy/config.alloy;
- добавляет пользователя alloy в необходимые группы;
- запускает alloy.service.

Production-инфраструктура при этом не используется.

Из WSL:

    cd /mnt/c/Users/Ivan/drg
    export ANSIBLE_CONFIG="$PWD/ansible.cfg"

Сначала проверить синтаксис:

    ansible-playbook \
      -i tests/logging/inventory.ini \
      tests/logging/logging-agent.yml \
      --syntax-check

Затем применить роль:

    ansible-playbook \
      -i tests/logging/inventory.ini \
      tests/logging/logging-agent.yml \
      --ask-become-pass

После установки:

    systemctl status alloy --no-pager
    curl http://127.0.0.1:12345/-/ready
    sudo alloy validate /etc/alloy/config.alloy
    journalctl -u alloy -n 100 --no-pager

Локальный Loki из Docker Compose должен продолжать работать на порту 3100.

При успешном первом запуске итог обычно содержит несколько changed-задач.
Повторите ту же команду: второй запуск должен завершиться с changed=0. Это
проверяет идемпотентность роли и отсутствие лишнего рестарта Alloy.

Проверить реальную доставку systemd journal:

    sudo systemctl restart alloy

Затем в Grafana Explore:

    {service="local-wsl", source="journal", unit="alloy.service"}

Docker-ветка роли включается только при наличии /var/run/docker.sock и группы
docker внутри самой WSL. Если Docker Desktop не предоставляет socket в WSL,
роль корректно оставит только сбор journal; Docker-путь при этом по-прежнему
проверяется контейнерным Alloy из основного smoke test.

## Повторный запуск

Повторный запуск безопасен:

    docker compose -f tests/logging/docker-compose.yml up -d

Compose обновит только изменившиеся контейнеры. Существующие данные останутся
в volumes.

После изменения локальных конфигураций можно принудительно пересоздать стенд:

    docker compose -f tests/logging/docker-compose.yml up -d --force-recreate

## Остановка и очистка

Остановить контейнеры, сохранив данные:

    docker compose -f tests/logging/docker-compose.yml down

Запустить их снова:

    docker compose -f tests/logging/docker-compose.yml up -d

Удалить контейнеры, сеть и все локальные данные стенда:

    docker compose -f tests/logging/docker-compose.yml down -v

Последняя команда безвозвратно удаляет локальные тестовые логи и состояние
локальной Grafana. Production-данные она не затрагивает.

Если выполнялся опциональный тест Ansible-роли, Alloy установлен непосредственно
в WSL и не удаляется командой docker compose down. Удалить его отдельно:

    sudo systemctl disable --now alloy
    sudo apt remove alloy

## Частые проблемы

### Порт 3000 или 3100 уже занят

Проверить:

    docker ps --format "table {{.Names}}\t{{.Ports}}"

Остановите конфликтующий локальный контейнер либо временно измените published
port в tests/logging/docker-compose.yml.

### Loki не становится healthy

    docker compose -f tests/logging/docker-compose.yml logs loki

Также выполните проверку конфигурации из раздела выше.

### В Grafana нет источника Loki

Проверьте:

    curl http://localhost:3000/api/datasources/uid/loki

Ответ должен содержать uid со значением loki.

### Запрос не возвращает строки

Проверьте последовательно:

    docker compose -f tests/logging/docker-compose.yml logs --tail 10 log-generator
    docker compose -f tests/logging/docker-compose.yml logs --tail 100 alloy
    curl http://localhost:3100/ready

После запуска Alloy может потребоваться несколько секунд для обнаружения
контейнеров и отправки первой пачки записей.
