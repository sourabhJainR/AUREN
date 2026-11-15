# ChatGPT Web and AUREN

AUREN remains local-first. The primary single-user workflow is the AUREN CLI, loopback MCP service and browser-based Engineering Console. The native AUREN Desktop shell starts that same local runtime and verifies it before showing the session as ready.

ChatGPT Web cannot call a workstation loopback URL directly. Use a hosted HTTPS MCP endpoint for a persistent connection, or the OpenAI Secure MCP Tunnel for controlled development. The tunnel does not turn a local endpoint into a public production service and does not change ChatGPT plan eligibility.

This guide covers ChatGPT Web. For ChatGPT desktop, use the shared Codex local stdio configuration in [Local Self-Hosted MCP](LOCAL_SELF_HOSTED_MCP.md#5-connect-chatgpt-desktop-without-oauth). That local connection does not require OAuth or a tunnel. A live ChatGPT desktop acceptance test is still pending.

## Plan and workspace requirements

Check the current OpenAI [custom MCP connection guide](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) before enabling the connection:

- Business, Enterprise and Edu workspaces support full custom MCP, subject to workspace permissions.
- Pro supports read and fetch tools only.
- Free is not currently listed as eligible for custom MCP connections.
- Custom MCP connections are documented for ChatGPT Web. The guide says they are not available on mobile.
- On Business, only workspace admins and owners can create and publish custom MCP apps. On Enterprise/Edu, admins can grant connection permission through RBAC; only admins and owners can publish.

AUREN's write tools must not be treated as available on a read-only plan. The server filters tools by the authenticated principal's scopes. Task submission and approval decisions also require their respective scopes; approval decisions require the configured approver identity allowlist.

## ChatGPT Free + GitHub Free workflow

This is the supported no-upgrade path. It does not create a live ChatGPT-to-AUREN connection: current OpenAI documentation does not list ChatGPT Free as eligible for custom MCP apps, and the ChatGPT GitHub app is read-only where it is available. Availability of the GitHub app in standard chat varies by plan and product surface.

Use ChatGPT Free as a reasoning and review assistant, and keep execution under AUREN on the laptop:

1. Install Git for Windows and GitHub CLI. Run `gh auth login` and complete browser authentication with the GitHub Free account. Do not paste a GitHub token into ChatGPT.
2. Clone any repository the account can access, or open an existing local checkout.
3. Run `auren up --project-root "C:\\Work\\YourProject"` (or run the source checkout's `python auren_cli.py up --project-root ...`). Use the local Engineering Console and AUREN CLI/MCP clients for project inspection, task tracking, Guard, approvals and verification.
4. In ChatGPT Free, provide only the files, logs or focused code excerpts needed for advice. If the GitHub app is offered in your account, connect it for repository reading only; do not expect it to push commits or create PRs.
5. Treat ChatGPT-generated code as a proposal. Apply and verify changes through AUREN's configured local coding host and approval workflow. If you apply a patch manually, AUREN cannot claim that the edit passed its Guard or approval path.
6. Use local Git/GitHub CLI for branch, commit, push, issue and pull-request operations. GitHub Free supports ordinary Git operations and public/private repositories, subject to the private-repository feature limits and the permissions of the signed-in account.

This route keeps ChatGPT Free and GitHub Free usable without a tunnel, paid API key, hosted server, or custom MCP connector. It is a human-in-the-loop workflow, not a direct tool integration. ChatGPT Free's usage limits still apply. See [GitHub's plan comparison](https://docs.github.com/en/get-started/learning-about-github/githubs-plans) and [OpenAI's GitHub connection guide](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt).

## Choose a connection path

### Primary path: local AUREN + ChatGPT desktop

Keep AUREN on the workstation. Start it with `auren up --project-root <path>` and connect ChatGPT desktop through its shared Codex stdio configuration. The stdio bridge reads the local operator token from AUREN's protected token file and forwards requests to `127.0.0.1`; the ChatGPT host config contains only local executable and path settings. **No OAuth, OpenAI API key, tunnel ID, public endpoint or hosted AUREN service is needed for this local path.** See [Local Self-Hosted MCP](LOCAL_SELF_HOSTED_MCP.md).

Do not expose the local MCP port directly to the internet. ChatGPT Web is a separate hosted product surface and cannot read local Codex configuration or reach workstation loopback directly.

### Private development tunnel

For optional ChatGPT Web access from a supported workspace, use OpenAI's official [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). The OpenAI `tunnel-client` runs beside AUREN and makes an outbound HTTPS connection. ChatGPT reaches the tunnel ID; it never connects directly to the laptop's loopback port. Do not expose the local MCP port to the internet. This tunnel is a transport relay, not a remote AUREN runtime.

For the Windows laptop workflow, use [AUREN local ChatGPT connection](CHATGPT_LOCAL_TUNNEL_WINDOWS.md). It starts one project-bound AUREN local runtime, waits for its authenticated MCP endpoint, writes a tunnel-client config with secret references, and runs the tunnel client in the foreground. The runtime API key is read from the process environment; the AUREN bearer token is read from its protected local token file. Stop the foreground process with Ctrl+C to stop both services.

The tunnel requires a provisioned `tunnel_id`, a runtime API key with tunnel-use permission, and the official tunnel-client binary. MCP request and response content transits OpenAI infrastructure. The tunnel client can read AUREN's local bearer token from a file reference and inject it on the final local MCP hop, but the tunnel is not a strict-local-auth or strict-local-data design. Use it only when that data path is acceptable. AUREN remains responsible for task admission, execution, approvals, Guard and evidence. The local MCP server is project-bound; stop and restart with another repository to switch projects.

Use a tunnel only when ChatGPT Web needs access and OpenAI transit of MCP request/response content is acceptable. The tunnel client uses a separate OpenAI Platform runtime API key and injects the local AUREN bearer credential on the final hop. No AUREN OAuth authorization server is needed when MCP OAuth is disabled in the ChatGPT tunnel connection. Do not use this path when credentials or MCP content must remain entirely outside OpenAI infrastructure. A hosted multi-user deployment remains a separate architecture and may use OAuth.

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

1. In ChatGPT Web, open Plugins.
2. Select the plus button, then choose Add custom MCP server.
3. Enter a name and the public HTTPS MCP endpoint URL. Use the Tunnel option only for a configured Secure MCP Tunnel.
4. For a hosted endpoint, configure OAuth and complete the identity-provider sign-in. For a Secure MCP Tunnel, select the tunnel and configure authentication according to the current connection UI. AUREN's local bearer credential is injected by the local tunnel client on the final MCP hop; the tunnel itself still requires its Platform runtime key. If the connection UI permits MCP authentication to be unset, no AUREN OAuth provider is needed for this local tunnel path. Do not treat this as strict-local data handling.
5. Review the risk warning and acknowledge it.
6. Select Create as a plugin, then review the discovered tools and metadata.
7. Confirm the tools match the scopes granted to the signed-in principal. Start with read-only tools; enable task submission and approval decisions only after the workspace plan, user scopes and approver allowlist are correct.

The current Plugins flow does not require enabling Developer Mode first. The legacy custom-app flow has different steps: workspace admins/owners use Workspace Settings > Apps > Create, while authorized users use Settings > Apps > Create. Follow the current OpenAI guide and workspace-admin policy because these controls can change.

The OpenAI guide documents ChatGPT Web, not a separate ChatGPT desktop acceptance path. AUREN's native Desktop shell is a separate local application.

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
