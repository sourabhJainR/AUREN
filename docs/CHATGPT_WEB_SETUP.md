# Use AUREN from ChatGPT Web and Codex CLI

**Status:** implementation and deployment guide. This document does not claim that a hosted endpoint, OAuth tenant, or ChatGPT workspace connection has already been deployed.

## Integrated execution path

AUREN now has one execution path for local and remote MCP task submission:

1. The MCP service authenticates the caller and binds the request to its configured project.
2. The built-in local composition admits the task, captures the source revision and Guard policy, and creates the canonical plan.
3. The canonical ExecutionController dispatches the registered Vibe Coding handler.
4. The Vibe Coding host runs its existing 18-stage workflow in an isolated Git worktree.
5. Repository edits wait for a durable AUREN approval. The ActionGovernanceGateway and durable side-effect ledger protect the execution.
6. AUREN verifies the host report, all 18 stage results, the base revision, worktree boundary, approval receipt and report digest before marking the task complete.

The source checkout is not modified by the Vibe Coding host. Merge, publish, release and production deployment are not part of this execution path.

The remote MCP launcher uses the same built-in canonical composition by default. A custom runtime factory remains available only for an explicitly managed deployment.

## Local installation and startup

Run:

~~~bash
auren up
~~~

After both local services are ready, AUREN opens the local console and ChatGPT Web. It also backs up and configures supported local MCP clients, including Codex CLI, unless --no-connect is supplied.

Use auren up --no-chatgpt to suppress the ChatGPT browser launch. Use auren up --no-browser to suppress the local console launch.

The local CLI connection uses the local authenticated MCP service. ChatGPT Web cannot reach a workstation's loopback endpoint directly, so opening ChatGPT does not connect it to local AUREN. For private use, Secure MCP Tunnel is a separate transport option described below.

## Configure the Vibe Coding host

The host reads its configuration from:

~~~text
~/.auren/vibe-coding/host.json
~~~

The config must be private on POSIX systems (mode 0600), use provider: "codex", explicitly set allow_workspace_writes: true, and define non-empty test commands. Keep the existing security and browser QA requirements enabled. Install and authenticate the Codex CLI on the machine that runs AUREN.

Check readiness:

~~~bash
auren doctor
~~~

The report includes vibe_coding_host:

- ready: Codex CLI and a valid host config are available; admitted tasks use the Vibe Coding handler.
- not_configured: local proposal-only provider behavior remains available, but remote repository execution is blocked.
- provider_missing or blocked: correct the CLI or host config before submitting implementation tasks.

The host configuration is an explicit operator opt-in to isolated-worktree edits. AUREN still requires a durable approval before the Vibe Coding handler starts.

## Local CLI use

auren up configures supported local MCP clients, including OpenAI's Codex CLI, to use the authenticated local MCP service. Verify the client configuration with:

~~~bash
codex mcp list
~~~

Submit tasks from Codex using the AUREN MCP server. Task admission, planning, approval, execution, checkpoints and evidence remain owned by AUREN.

There is no separate official ChatGPT command-line client in this integration. The supported OpenAI coding CLI path is Codex CLI.

## ChatGPT Web requires a deployed remote endpoint

ChatGPT Web cannot call 127.0.0.1, localhost, or a developer workstation's local filesystem. Deploy the remote MCP service on a host that has the repository workspace, the Vibe Coding host config and the Codex CLI installed.

The repository does not contain a live hosted AUREN endpoint, an OAuth tenant/client registration, or deployment credentials. ChatGPT Web connection is therefore a deployment step, not something auren up can complete on its own.

### Private development with Secure MCP Tunnel

OpenAI Secure MCP Tunnel can carry a private MCP server to supported OpenAI products without opening inbound firewall ports. It is a transport path, not an OAuth provider or a replacement for AUREN's authentication and authorization checks.

Use it for private development and connection testing when the Platform organization and ChatGPT workspace have the required tunnel permissions. The tunnel client must run inside the network that can reach AUREN. Keep the AUREN remote service bound to loopback and follow the current [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

Important boundaries:
- Tunnel permissions in the OpenAI Platform organization and custom MCP permissions in the ChatGPT workspace are separate.
- A tunnel does not make an ineligible ChatGPT plan eligible for custom MCP.
- OAuth discovery can pass through the tunnel, but the authorization server is not automatically tunneled. It must remain reachable by the browser-facing OAuth flow.
- Keep AUREN's resource URI, OAuth issuer, token audience, allowed host and tunnel routing consistent. Verify the protected-resource metadata and a real tool call; do not guess the resource URI or audience from the tunnel ID.
- Secure MCP Tunnel is for private testing and use. It does not replace the stable public HTTPS endpoint required for public plugin submission or distribution.

### Remote service setup

Install the optional ASGI runner:

~~~bash
python -m pip install "uvicorn[standard]"
~~~

Configure the project and OAuth resource-server environment through the deployment's secret manager:

~~~bash
export AUREN_PROJECT_ROOT=/srv/auren/workspaces/main
export AUREN_MCP_RESOURCE_URL=https://mcp.example.com/mcp
export AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
export AUREN_MCP_INTROSPECTION_URL=https://identity.example.com/oauth/introspect
export AUREN_MCP_INTROSPECTION_CLIENT_ID=auren-mcp-resource
export AUREN_MCP_INTROSPECTION_CLIENT_SECRET="$(secret-manager read auren/mcp/introspection-secret)"
export AUREN_MCP_ALLOWED_HOSTS=mcp.example.com
export AUREN_MCP_ALLOWED_ORIGINS=https://chatgpt.com
export AUREN_MCP_REMOTE_EXECUTION_ENABLED=1
export AUREN_MCP_APPROVER_SUBJECT_DIGESTS="$(secret-manager read auren/mcp/approver-subject-digests)"

python -m portable.remote_mcp_server \
  --project-root "$AUREN_PROJECT_ROOT" \
  --host 127.0.0.1 --port 8765
~~~

The launcher creates the built-in canonical runtime composition when --runtime-factory is omitted. Keep the ASGI listener on loopback behind a TLS ingress. Use persistent local disk and one service replica; the current SQLite stores are not a multi-host deployment.

Remote task submission requires all of the following:

- OAuth introspection returns an HTTPS issuer, subject, auren:tasks:write scope, engineering_manager role, and an explicit projects claim containing the exact server-side project path.
- AUREN_MCP_REMOTE_EXECUTION_ENABLED=1 is set on the AUREN host.
- The host's Vibe Coding config is valid and Codex CLI is available.
- A separate approved identity can approve repository edits. It must have the engineering_manager role, auren:approvals:decide scope and the same exact project claim. Its principal digest must be in AUREN_MCP_APPROVER_SUBJECT_DIGESTS.

Calculate an approver digest from the exact issuer and subject claims returned by the identity provider:

~~~bash
python - <<'PY'
import hashlib
issuer = "https://identity.example.com/"
subject = "approver-subject-claim-from-idp"
print(hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest())
PY
~~~

Do not put raw subject claims, OAuth tokens or client secrets in repository files. The digest allowlist is an explicit host-side approval policy. Remote task requesters cannot approve their own work; configure a separate engineering-manager approver.

### OAuth provider requirements

ChatGPT uses an OAuth authorization-code flow with PKCE. Configure an established identity provider; do not build a custom authorization server for this integration.

- Publish authorization-server discovery metadata with the exact issuer URI.
- Support authorization code with PKCE S256 and the client identification method required by the ChatGPT connector. OpenAI documents CIMD with `none` or `private_key_jwt`, and DCR where needed.
- Issue refresh tokens and advertise `offline_access` (or the provider's equivalent) so the connection can renew access.
- Echo the requested MCP `resource` into the access token's audience/resource claim.
- The introspection response must return `iss`, `sub`, `aud`, `exp` and `scope`; AUREN compares issuer and resource identifiers exactly, including trailing slashes.
- Add AUREN-specific `roles` and `projects` claims for task execution and approval policies.

The ChatGPT OAuth client and AUREN's introspection client are separate credentials. Store the introspection client secret only in the host secret manager. Do not put it in ChatGPT app settings or source control.

Follow [Remote MCP Operations](REMOTE_MCP_OPERATIONS.md) and [Remote MCP Privacy](REMOTE_MCP_PRIVACY.md) before exposing the endpoint.

## ChatGPT workspace eligibility

Custom MCP availability is controlled by the ChatGPT plan and workspace permissions:

- Business and Enterprise/Edu support full MCP on ChatGPT web, including write tools, subject to workspace administration.
- Pro can connect custom MCP servers with read/fetch permissions only. It cannot use AUREN's task submission or approval tools through ChatGPT.
- Free is not listed as a supported plan for custom MCP server connections.
- The documented custom MCP connection flow is for ChatGPT web. Do not assume the same custom-server support in mobile or desktop clients without a separate live test.

AUREN cannot change plan eligibility or workspace permissions. Keep the local CLI, MCP and Engineering Console as the primary path when the ChatGPT account does not support the required MCP permissions.

## Run the read-only hosted acceptance probe

Before connecting ChatGPT, run the live acceptance script from a trusted operator machine. It verifies HTTPS health/readiness, protected-resource metadata, OAuth discovery (exact issuer, PKCE S256, supported client registration and refresh), MCP protocol discovery, per-tool OAuth declarations, the unauthenticated OAuth challenge and stable `get_profile` identity. It does not submit tasks or approve work.

Set the values locally; do not paste access tokens into chat or commit them:

~~~bash
export AUREN_MCP_ENDPOINT=https://mcp.example.com/mcp
export AUREN_MCP_ALLOWED_HOSTS=mcp.example.com
export AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
export AUREN_MCP_ACCESS_TOKEN="$(secret-manager read auren/mcp/test-access-token)"
python scripts/remote_mcp_acceptance.py
~~~

For a token that includes the task-write and approval scopes, also verify that the corresponding write tools and scopes are discoverable:

~~~bash
python scripts/remote_mcp_acceptance.py --require-write
~~~

The report confirms metadata and tool readiness only. It deliberately performs zero task mutations. A separate controlled end-to-end task test is still required before enabling write actions for a workspace.

## Connect the remote service in ChatGPT Web

After HTTPS, OAuth and the remote MCP service are live, and the ChatGPT workspace permits custom MCP apps:

1. Open ChatGPT Web and go to Plugins.
2. Select +, then Add custom MCP server.
3. Name the connection AUREN.
4. Enter the deployed HTTPS endpoint, for example https://mcp.example.com/mcp.
5. Select OAuth and complete the identity-provider sign-in.
6. Review the discovered tools and their permissions before installing the app.
7. Start a new conversation and select AUREN for the message that should use its tools.

The current ChatGPT UI and workspace policy may differ. See [OpenAI's connection guide](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## Start with read-only access

For the first connection, grant only read scopes and enable:

- auren.get_profile
- auren.service_status
- auren.list_tasks
- auren.get_task
- auren.get_evidence
- auren.list_approvals
- auren.get_approval

Verify the identity, service health, task visibility and evidence summaries. Then review the task-write and approval scopes, OAuth claims, project binding, separate-approver policy and audit chain before enabling implementation.

A ChatGPT confirmation prompt is not a replacement for AUREN Guard or durable approval.

## Verification boundary

A successful MCP connection or accepted task receipt is not proof that engineering work completed. Completion requires the canonical controller task, approved action receipt, accepted 18-stage host report, source-revision binding, isolated worktree checks and verified evidence digest.

Before calling the hosted integration production-ready, also complete:

- OAuth authorization, refresh, revocation and issuer/audience negative tests.
- Workspace escape, secret access, process failure, cancellation and restart-recovery tests.
- Remote MCP acceptance through the deployed endpoint and real ChatGPT tool discovery.
- Exact-head CI and post-deployment smoke tests.
- Backup, restore, retention and single-host recovery rehearsal.

The built-in remote launcher removes the custom runtime-factory requirement. It does not deploy the service, create OAuth credentials, configure a ChatGPT workspace app or prove multi-host readiness.
