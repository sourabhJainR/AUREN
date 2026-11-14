# AUREN remote MCP single-host deployment

This Compose bundle runs the authenticated AUREN remote MCP service behind Caddy. Caddy owns public TLS; the MCP container is reachable only on the private Docker network. The service uses the canonical AUREN runtime composition and persistent local state.

This is a single-host, single-replica deployment. It is not a multi-tenant hosted service or an HA configuration. Do not scale the MCP container horizontally: its runtime and control state use local durable stores.

## Requirements

- A Linux Docker host with Docker Compose v2.
- A DNS A/AAAA record for the MCP hostname pointing to the host.
- Inbound TCP 80 and 443 (and optional UDP 443 for HTTP/3). Do not publish port 8765.
- An existing project checkout or workspace directory on the host.
- An external OAuth authorization server with discovery, authorization code + PKCE S256, CIMD or DCR, refresh-token support and token introspection.
- AUREN provider credentials and execution configuration for the agents you intend to run.

## Configure

From the repository root:

~~~bash
cp deploy/remote-mcp/.env.example deploy/remote-mcp/.env
chmod 600 deploy/remote-mcp/.env
mkdir -p /srv/auren/project
~~~

Set ownership so the container user (UID/GID 10001) can read and write the workspace:

~~~bash
sudo chown -R 10001:10001 /srv/auren/project
~~~

Edit `deploy/remote-mcp/.env`:

- Set `AUREN_MCP_DOMAIN` to the DNS name that points to this host.
- Set `AUREN_PROJECT_HOST_PATH` to the absolute workspace path.
- Set `AUREN_MCP_RESOURCE_URL` to the exact HTTPS resource URI, with no trailing slash. Its path must match `/mcp`.
- Set `AUREN_MCP_AUTHORIZATION_SERVER` to the exact issuer from the identity provider metadata. A trailing slash is significant.
- Set the introspection URL, client ID and secret. The secret must be private and rotated through the secret manager.
- Keep `AUREN_MCP_ALLOWED_HOSTS` limited to the MCP hostname. Do not add the identity provider to the incoming Host allowlist.
- Set the allowed browser origin to the exact ChatGPT web origin approved for your workspace.
- Add provider credentials only when required by the chosen AUREN runtime providers.

The sample values are placeholders. Do not start the service with them. Before enabling remote approval decisions, configure `AUREN_MCP_APPROVER_SUBJECT_DIGESTS` with the SHA-256 digest of the exact issuer string, a NUL byte, and the authenticated subject. The issuer string is not normalized; its trailing slash matters. Never store an email address or raw subject in the allowlist.

## Start and verify

~~~bash
docker compose -f deploy/remote-mcp/compose.yaml config --quiet
docker compose -f deploy/remote-mcp/compose.yaml up -d --build
docker compose -f deploy/remote-mcp/compose.yaml ps
docker compose -f deploy/remote-mcp/compose.yaml logs -f auren-mcp
~~~

Caddy obtains and renews the TLS certificate automatically. Only Caddy publishes host ports 80/443. The MCP service binds to 0.0.0.0 inside the private Docker network; `AUREN_MCP_ALLOW_PUBLIC_BIND=1` is set because of that container bind. The application port is not published to the host.

Caddy has the fixed private address `172.30.0.2`; Uvicorn trusts forwarded HTTPS headers only from that address. If the subnet conflicts with an existing Docker network, change the Compose subnet, both static addresses and `AUREN_MCP_FORWARDED_ALLOW_IPS` together.

Run the live OAuth/MCP acceptance probe from a trusted operator machine after deployment. Include the MCP hostname and the OAuth issuer, authorization, token and registration endpoint hosts in the probe's outbound allowlist. Use a short-lived read-only access token first; use `--require-write` only with a full-scope token. The probe performs no task mutations:

~~~bash
export AUREN_MCP_ENDPOINT=https://mcp.example.com/mcp
export AUREN_MCP_ALLOWED_HOSTS=mcp.example.com,identity.example.com
export AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
export AUREN_MCP_ACCESS_TOKEN="$(secret-manager read auren/mcp/test-access-token)"
python scripts/remote_mcp_acceptance.py
~~~

## Operations and limits

- Keep exactly one MCP replica. Back up the named `auren_state` volume and the project workspace. Use SQLite's backup API or stop the service before taking a filesystem snapshot; do not copy a live SQLite database file.
- Back up Caddy's `caddy_data` and `caddy_config` volumes so certificate state can be restored.
- Monitor container restarts, `/healthz`, `/readyz`, OAuth introspection failures, queue depth and task age. The Compose health check is TCP-only; the HTTPS acceptance probe checks application readiness.
- Rotate the introspection secret and identity-provider signing keys using the identity provider's documented process.
- Do not expose port 8765, mount Docker socket, run the MCP container as root, or put credentials in the image.
- The project workspace is writable by the AUREN process. Limit it to the intended repository and keep backups outside the container.
- This bundle does not provide multi-host failover, horizontal scaling, managed database backups, code signing, or a managed OAuth tenant.
- A hosted MCP endpoint does not make ChatGPT plan features available. Full MCP tools require a supported Business or Enterprise/Edu workspace; Pro is read/fetch only. Free is not listed as eligible for custom MCP connections.

For the full OAuth, retention, recovery and security runbook, see [Remote MCP Operations](../../docs/REMOTE_MCP_OPERATIONS.md) and [ChatGPT Web Setup](../../docs/CHATGPT_WEB_SETUP.md).
