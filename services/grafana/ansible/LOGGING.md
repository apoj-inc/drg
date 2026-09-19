# Centralized logging

The stack consists of a single-node Loki instance deployed with the Grafana
Compose project and Grafana Alloy agents installed on runtime service hosts.

    Docker stdout/stderr --\
                           Alloy -> Loki -> Grafana Explore
    systemd journal ------/

Loki listens on port 3100 of the Grafana runtime host. The port is intended for
the internal application network; do not expose it through the public Traefik
route without authentication. Loki stores data in the grafana_loki-data Docker
volume and applies the retention configured in services/grafana/config.yaml.

## Deploy the central stack

Run from the repository root after exporting the normal Grafana and NetBox
environment variables:

    ansible-playbook -i shared/ansible/inventory/netbox.yml \
      services/grafana/ansible/grafana.yml \
      --limit tags_grafana

Verify on the Grafana host:

    cd /opt/grafana
    docker compose ps

In Grafana, open Explore, select Loki, and initially run:

    {service=~".+"}

## Roll out Alloy safely

Alloy is deployed by the normal playbook for each runtime service. Start with
Grafana so Loki is available, then deploy one application:

    ansible-playbook -i shared/ansible/inventory/netbox.yml \
      services/keycloak/ansible/keycloak.yml \
      --limit tags_keycloak

The same service playbook installs the application and its logging_agent. The
role discovers the Loki address from the Grafana inventory group tags_grafana.
Docker logs are enabled only where both the Docker socket and docker group
exist. Journal collection uses the adm and systemd-journal groups and keeps
only the units declared in the service's logging.journal_units list. Unit names
in service config omit the .service suffix; Alloy adds it when rendering the
filter. If the logging section is absent, journal ingestion is disabled for
that service.

## Useful LogQL queries

All logs for one service:

    {service="keycloak"}

Errors from one service:

    {service="keycloak"} |= "error"

Journal records for one unit:

    {source="journal", unit="docker.service"}

Logs from one Docker Compose service:

    {source="docker", compose_service="keycloak"}

## Current scope

The initial role collects selected systemd units and Docker container
stdout/stderr. Application-specific files such as /var/log/gitlab and FreeIPA
logs require additional loki.source.file definitions and are not part of this
first rollout.
