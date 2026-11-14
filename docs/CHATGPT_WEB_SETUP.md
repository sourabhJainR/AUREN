# ChatGPT Web and AUREN

AUREN remains local-first. The primary single-user workflow is the AUREN CLI, loopback MCP service and browser-based Engineering Console. The native AUREN Desktop shell starts that same local runtime and verifies it before showing the session as ready.

ChatGPT Web cannot call a workstation loopback URL directly. Use a hosted HTTPS MCP endpoint for a persistent connection, or the OpenAI Secure MCP Tunnel for controlled development. The tunnel does not turn a local endpoint into a public production service and does not change ChatGPT plan eligibility.

This guide covers ChatGPT Web. The native AUREN Desktop shell is a separate application; a live ChatGPT Desktop MCP acceptance test has not been completed.

## Plan and workspace requirements

Check the current OpenAI [MCP server documentation](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) before enabling the connection:

- Business, Enterprise and Edu workspaces support the full custom MCP tool experience, subject to workspace-admin controls.
- Pro supports read and fetch tools only.
- Free is not currently listed as eligible for custom MCP connections.

AUREN's write tools must not be treated as available on a read-only plan. The server filters tools by the authenticated principal's scopes. Task submission and approval decisions also require their respective scopes; approval decisions require the configured approver identity allowlist.

## Choose a connection path

### Local development

Keep the local MCP endpoint bound to loopback and use a local MCP client such as Codex CLI or another supported host. Use the browser Engineering Console for the local dashboard and task monitoring.

Do not expose the local MCP port directly to the internet. ChatGPT Web cannot reach a workstation loopback endpoint without a tunnel or hosted service.

### Private development tunnel

For a temporary development connection, follow the official [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). It provides a private path from ChatGPT to a local MCP service without opening the MCP port on the public network.

Use a dedicated development environment and short-lived credentials. Do not treat a tunnel session as a production deployment or as a substitute for OAuth, workspace permissions, backups or monitoring.

### Hosted endpoint

For persistent use, deploy the remote MCP service at a stable HTTPS URL. The repository includes a single-host Docker Compose deployment behind Caddy:

- [Remote MCP deployment bundle](../deploy/remote-mcp/README.md)
- [Remote MCP operations and security runbook](REMOTE_MCP_OPERATIONS.md)

The reference deployment is single-host and single-replica. It uses persistent local state and is not an HA or multi-tenant service.

## Configure the hosted service

The resource URL must match the MCP endpoint path exactly. Do not add a trailing slash:

~~~text
AUREN_MCP_RESOURCE_URL=https://mcp.example.com/mcp
AUREN_MCP_ENDPOINT_PATH=/mcp
AUREN_MCP_ALLOWED_HOSTS=mcp.example.com
AUREN_MCP_ALLOWED_ORIGINS=https://chatgpt.com
~~~

Configure the identity provider with its exact issuer URI. A trailing slash is significant and must match the issuer returned by its discovery metadata and token introspection:

~~~text
AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
AUREN_MCP_INTROSPECTION_URL=https://identity.example.com/oauth/introspect
AUREN_MCP_INTROSPECTION_CLIENT_ID=auren-mcp-resource-server
AUREN_MCP_INTROSPECTION_CLIENT_SECRET=<load from a secret manager>
~~~

The identity provider must support:

- Authorization Code flow with PKCE S256.
- Client ID Metadata Documents (CIMD) or Dynamic Client Registration.
- A public client method such as `none` or `private_key_jwt`.
- Refresh-token support or the `offline_access` scope.
- Token introspection that returns the exact issuer, audience, subject, expiry and granted scopes. AUREN also uses role and project claims for authorization.

The protected-resource metadata must publish the exact authorization-server issuer. Do not normalize issuer or resource URLs by removing trailing slashes.

Keep `AUREN_MCP_ALLOWED_HOSTS` limited to the public MCP hostname. It is the incoming Host-header allowlist; do not add the identity provider to it.

If the remote service exposes approval decisions, configure `AUREN_MCP_APPROVER_SUBJECT_DIGESTS` with the SHA-256 digest of the exact issuer string, a NUL byte and the authenticated subject. Do not store an email address or raw subject. The exact issuer string, including its trailing slash, is part of the digest input.

Do not configure static bearer tokens for a hosted deployment.

## Run the live acceptance probe

Run this from a trusted operator machine after deployment. Use a short-lived access token for the target resource. Keep it in the environment; do not put it in command-line arguments, source control or chat.

~~~bash
export AUREN_MCP_ENDPOINT=https://mcp.example.com/mcp
export AUREN_MCP_ACCEPTANCE_ALLOWED_HOSTS=mcp.example.com,identity.example.com
export AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
export AUREN_MCP_ACCESS_TOKEN="$(secret-manager read auren/mcp/test-access-token)"
python scripts/remote_mcp_acceptance.py
~~~

The acceptance probe checks:

- HTTPS health and readiness.
- Exact resource and issuer metadata.
- OAuth authorization-server discovery, PKCE S256, client registration support and refresh capability.
- Explicit allowlisting of the MCP and identity-provider hosts.
- MCP modern discovery and legacy initialization fallback.
- Required read-only tools and their OAuth scopes.
- The unauthenticated ChatGPT OAuth challenge.
- Stable `get_profile` identity and structured output.

It calls only health, discovery, tool-list and profile-read operations. It performs zero task mutations.

To verify task submission and approval-decision tool declarations with a full-scope token, run:

~~~bash
python scripts/remote_mcp_acceptance.py --require-write
~~~

This verifies tool visibility and declared scopes; it still does not submit a task or approve work. A controlled end-to-end task test in a disposable project is a separate release gate.

## Connect from ChatGPT Web

After the hosted endpoint and OAuth provider pass acceptance:

1. In ChatGPT Web, enable the workspace's custom MCP/developer app capability if the workspace administrator permits it.
2. Create a custom app and enter the public MCP endpoint URL.
3. Select the OAuth authentication flow and complete the identity-provider sign-in.
4. Confirm that the tools shown by ChatGPT match the scopes granted to the signed-in principal.
5. Start with read-only tools. Enable task submission and approval decisions only after the workspace plan, user scopes and approver allowlist are correct.

The exact settings and availability can change by workspace and plan. Follow the current OpenAI documentation and workspace-admin guidance rather than assuming the same controls exist on every account.

## Release boundary

A successful acceptance probe proves that the deployed server meets the tested OAuth and MCP metadata contract. It does not prove that a real ChatGPT workspace has completed authorization or that every tool works in the ChatGPT UI.

Do not mark hosted ChatGPT integration production-ready until all of the following are complete:

- The public endpoint and OAuth tenant are deployed and configured.
- The live acceptance probe passes on the deployed release commit.
- A supported ChatGPT workspace connects and completes OAuth.
- Read-only tool calls succeed in ChatGPT.
- A controlled task submission, approval, cancellation and recovery test succeeds with the intended scopes.
- Monitoring, backup/restore, secret rotation and incident runbooks are tested.

See [MCP host compatibility](MCP_HOST_COMPATIBILITY.md), [remote MCP operations](REMOTE_MCP_OPERATIONS.md) and the [OpenAI MCP guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).
