# OAuth2 Proxy

OAuth2 Proxy provides the Keycloak-backed forward-auth endpoint used by
Traefik. It runs in authentication-only mode with a cookie session store and
does not proxy application traffic. Two replicas are deployed; Traefik uses
`/ping` to remove an unhealthy replica from the public route. Prometheus
metrics are exposed only on the internal host port 44180.

OIDC discovery uses the public Keycloak issuer, which is itself backed by two
health-checked Keycloak replicas. Direct Keycloak URLs are intentionally left
disabled unless an isolated bootstrap operation explicitly enables discovery
skipping.

Keycloak client:

- Client ID: `oauth2-proxy`
- Issuer: `https://keycloak.example.test/realms/drg`
- Redirect URI: `https://oauth2.example.test/oauth2/callback`

Required secrets in `kv/services/oauth2-proxy`:

- `keycloak_oauth2_proxy_client_secret` (must match the Keycloak realm client secret)
- `oauth2_proxy_cookie_secret`

Existing Authentik middleware remains available in Traefik. Routes opt into
the Keycloak middleware independently.
