# AUREN Remote MCP Operations

This is the deployment and client integration guide for AUREN's authenticated remote MCP service. The service is an operator interface to AUREN. It does not replace the supervisor, execution controller, evidence store or approval authority.

## Current implementation

Before public deployment, review [REMOTE_MCP_PRIVACY.md](REMOTE_MCP_PRIVACY.md), publish the deployment-specific privacy/retention terms, and configure the GitHub security reporting route.

- ASGI Streamable HTTP endpoint: POST /mcp.
- Protocol versions: 2026-07-28 stateless (preferred) plus legacy 2025-11-25, 2025-06-18 and 2025-03-26.
- Stateless transport with no MCP session affinity or server-side session ID.
- JSON responses for requests; notifications receive HTTP 202.
- GET returns HTTP 405 because this service does not open an SSE stream.
- OAuth resource-server validation through a trusted external authorization server's token-introspection endpoint.
- Static bearer tokens are limited to development/private-network use and require explicit opt-in.
- Protected-resource metadata: /.well-known/oauth-protected-resource.
- Liveness/readiness endpoints: /healthz and /readyz.
- Operator control and rate-limit state are stored under canonical AUREN home on persistent local disk.

The service does not implement an OAuth authorization server. An established identity provider must implement OAuth 2.1 authorization-code flow with PKCE S256, issuer discovery, Client ID Metadata Documents (CIMD; preferred by MCP 2026-07-28) or DCR for legacy clients, refresh/revocation and the resource/audience parameter. Authorization responses must include and validate the RFC 9207 `iss` parameter; credentials must remain bound to the issuer that minted them. The MCP resource server validates the resulting token through introspection on each request. The introspection response must include iss, sub, aud, exp and scope; roles and projects should also be included where those policies are used. Certificate-bound access tokens (cnf claims) are rejected because the ASGI service does not validate token-to-client-certificate binding.

## Operator tool surface

| Tool | Permission | Effect |
|---|---|---|
| auren.get_profile | auren:tasks:read | Stable opaque identity for the authenticated connection |
| auren.service_status | auren:tasks:read | Queue, health, dispatcher and admission status |
| auren.submit_task | auren:tasks:write | Durable task acceptance in the server-configured workspace; requires a caller-scoped idempotency key |
| auren.list_tasks | auren:tasks:read | Bounded task and trigger inventory |
| auren.get_task | auren:tasks:read | Task/trigger status and progress |
| auren.cancel_task | auren:tasks:cancel | Cancel pending triggers or signal a live runtime guard |
| auren.get_evidence | auren:evidence:read | Bounded progress events and evidence references |
| auren.list_approvals | auren:approvals:read | Durable approval inventory |
| auren.get_approval | auren:approvals:read | Approval status and immutable digests |
| auren.decide_approval | auren:approvals:decide | Decision using authenticated subject and trusted host approver policy |
| auren.resume_provider_task | auren:tasks:resume | Resume only the provider reconciliation path; host authorizer remains mandatory |
| auren.pause_admissions | auren:admin | Stop accepting new MCP tasks; does not stop active work |
| auren.resume_admissions | auren:admin | Reopen MCP task admission |

Every tool publishes OAuth securitySchemes metadata. ChatGPT-specific profile metadata is provided by auren.get_profile. The profile ID is generated once per authenticated subject and persisted; it does not contain the subject or email.

Tool schemas are validated again by the server. Unknown arguments are rejected. The workspace root is configured by the host and cannot be supplied by the model. There is no general shell, arbitrary file-write, arbitrary network-fetch or unrestricted provider tool.

### Cancellation and pause semantics

- Pending or retryable unclaimed trigger events can be cancelled with a durable status transition.
- Claimed adaptive-runtime work receives a cancellation signal through its live RuntimeExecutionGuard. The trigger remains nonterminal until the running supervisor records its outcome.
- Controller tasks use the existing ExecutionController.request_cancellation path.
- If a claimed task has no live guard, the service reports that cancellation was not accepted. It never reports an unconfirmed cancellation as complete.
- Admission pause affects new MCP submissions only. It does not pause tasks submitted through other AUREN interfaces or stop work already running.
- Provider resume is limited to the existing blocked-provider reconciliation path. It does not replay an unknown external side effect.

## Runtime host contract

The default remote launcher creates AUREN's built-in single-host composition without a custom factory. It uses the same canonical ExecutionController, TriggerRuntime, supervisor, Guard, plan store, approval store, durable side-effect ledger and host-owned operator identity as the local MCP host.

The built-in composition connects the existing 18-stage Vibe Coding host when its user config is valid and the Codex CLI is installed. The handler is registered as approval-gated. It creates a durable approval request before starting the host, runs only in an isolated Git worktree, and returns a report that AUREN verifies against the admitted source revision. The source checkout is not modified, and merge/publish/release/production deployment are not part of this handler.

The Vibe host config is read from ~/.auren/vibe-coding/host.json. It must explicitly enable isolated-worktree writes, use the Codex CLI workspace-write sandbox, define test commands and retain the required security/browser QA gates. If the host is not ready, remote task admission fails closed. The local host can still use its proposal-only model path when no Vibe host is configured.

Remote task admission additionally requires:
- AUREN_MCP_REMOTE_EXECUTION_ENABLED=1 on the AUREN host.
- An introspected HTTPS issuer and subject.
- The auren:tasks:write scope and engineering_manager role.
- An explicit projects claim containing the exact server-side project path.
- A separate approved identity for remote repository-edit approvals, with the engineering_manager role, auren:approvals:decide scope and the same exact project claim. Configure its issuer/subject digest in AUREN_MCP_APPROVER_SUBJECT_DIGESTS.

Local single-user requests can be approved by the host's local operator identity. Remote requests enforce separation of duties and cannot be approved by the same principal that submitted them.

A custom runtime factory remains available for an explicitly managed deployment. It must return an initialized AdaptiveRuntime-compatible host with a non-empty reviewed graph, the canonical ExecutionController and TriggerRuntime, configured provider adapters, host-side authorization and evidence verification, and durable state on persistent local storage. Its signature is:

~~~python
def create_runtime(*, project_root: Path) -> AdaptiveRuntime:
    ...
~~~

## Deployment

Install the optional ASGI runner:

~~~bash
python -m pip install "uvicorn[standard]"
~~~

Configure the project and resource server. The built-in canonical composition is the default; set AUREN_MCP_RUNTIME_FACTORY only when using an explicitly managed custom runtime:

~~~bash
export AUREN_PROJECT_ROOT=/srv/auren/workspaces/main
export AUREN_MCP_RESOURCE_URL=https://mcp.example.com/mcp
export AUREN_MCP_AUTHORIZATION_SERVER=https://identity.example.com/
export AUREN_MCP_INTROSPECTION_URL=https://identity.example.com/oauth/introspect
export AUREN_MCP_INTROSPECTION_CLIENT_ID=auren-mcp-resource
export AUREN_MCP_INTROSPECTION_CLIENT_SECRET="$(secret-manager read auren/mcp/introspection-secret)"
export AUREN_MCP_ALLOWED_HOSTS=mcp.example.com
export AUREN_MCP_ALLOWED_ORIGINS=https://your-approved-browser-origin.example
export AUREN_MCP_CONTROL_DB=/var/lib/auren/mcp/operator-control.sqlite3
export AUREN_MCP_REMOTE_EXECUTION_ENABLED=1
export AUREN_MCP_APPROVER_SUBJECT_DIGESTS=<approved-approver-digest>

python -m portable.remote_mcp_server \
  --project-root "$AUREN_PROJECT_ROOT" \
  --host 127.0.0.1 --port 8765
~~~

For remote execution, provision the Vibe host config at ~/.auren/vibe-coding/host.json on this server and install/authenticate Codex CLI. The OAuth token must contain the exact project path, engineering_manager role and auren:tasks:write scope. The separate approver digest is SHA-256 of issuer.rstrip("/") + NUL + subject; calculate it from the identity provider's exact claims and keep only the digest in the host environment.

The shell command illustrates environment names. Replace the browser-origin placeholder with exact origins observed and approved for your clients; native MCP clients may not send an Origin header. Do not guess vendor origins. Use the secret manager supplied by the deployment. Do not put tokens or client secrets in repository files, MCP client configuration, command-line arguments or logs.

Place a TLS reverse proxy in front of the loopback listener. Configure it to:
- Terminate TLS and reject invalid hostnames.
- Set X-Forwarded-Proto: https only from the trusted proxy.
- Forward client IP/host/proto only from known proxy addresses.
- Apply edge rate limits, request timeouts and body-size limits.
- Avoid logging Authorization headers or request/response bodies.
- Set AUREN_MCP_FORWARDED_ALLOW_IPS to the actual proxy addresses only.

Do not bind to 0.0.0.0 or :: unless AUREN_MCP_ALLOW_PUBLIC_BIND=1 is explicitly set and network/TLS controls are already in place. The default bind is loopback.

### Environment variables

| Variable | Required | Purpose |
|---|---:|---|
| AUREN_MCP_RUNTIME_FACTORY | No | Optional trusted module:function factory; omitted for the built-in canonical composition |
| AUREN_MCP_REMOTE_EXECUTION_ENABLED | Remote writes | Must be 1 before remote task submission can use the Vibe Coding host |
| AUREN_MCP_APPROVER_SUBJECT_DIGESTS | Remote writes | Comma-separated SHA-256 issuer/subject digests for separate engineering-manager approvers |
| AUREN_PROJECT_ROOT | Yes | Workspace root fixed by the host |
| AUREN_MCP_RESOURCE_URL | Yes | Canonical HTTPS MCP resource URL and token audience |
| AUREN_MCP_AUTHORIZATION_SERVER | OAuth | External OAuth issuer URL |
| AUREN_MCP_INTROSPECTION_URL | OAuth | HTTPS token introspection endpoint |
| AUREN_MCP_INTROSPECTION_CLIENT_ID | OAuth | Resource-server client ID |
| AUREN_MCP_INTROSPECTION_CLIENT_SECRET | OAuth | Resource-server secret from secret manager |
| AUREN_MCP_ALLOWED_HOSTS | Yes | Exact public hostnames |
| AUREN_MCP_ALLOWED_ORIGINS | Browser clients | Exact browser origins; never wildcard |
| AUREN_MCP_CONTROL_DB | No | Persistent control/rate-limit database path |
| AUREN_MCP_MAX_PENDING_TASKS | No | Maximum pending/claimed/retryable MCP trigger events; defaults to 1000 |
| AUREN_MCP_MAX_RESPONSE_BYTES | No | Maximum MCP response size; defaults to 2 MiB |
| AUREN_MCP_ENDPOINT_PATH | No | MCP path; defaults to /mcp |
| AUREN_MCP_BIND_HOST | No | Defaults to 127.0.0.1 |
| AUREN_MCP_PORT | No | Defaults to 8765 |
| AUREN_MCP_FORWARDED_ALLOW_IPS | No | Trusted reverse-proxy addresses only |

Static token mode is intended for local development or a trusted private network:

~~~bash
export AUREN_MCP_ALLOW_STATIC_TOKENS=1
export AUREN_MCP_STATIC_TOKENS='[
  {
    "token": "<random-secret-at-least-32-characters>",
    "subject": "developer-1",
    "scopes": ["auren:tasks:read", "auren:tasks:write"],
    "roles": ["engineering_manager"],
    "projects": ["/srv/auren/workspaces/main"]
  }
]'
~~~

Static mode is not the ChatGPT OAuth integration path. For public hosted clients, use OAuth authorization-code + PKCE through an established identity provider.

## Client connection examples

The remote endpoint is the same for every MCP-capable client: https://mcp.example.com/mcp. Use OAuth where the client supports it. Do not embed a long-lived bearer token in a shared project file.

### Codex CLI and Codex app

The Codex CLI and app share MCP configuration:

~~~bash
codex mcp add auren --url https://mcp.example.com/mcp
codex mcp list
~~~

Use the authentication flow supported by the installed Codex version. For private development, keep any static bearer token in the user's local secret environment, not the repository.

### Claude Code CLI, VS Code extension and Desktop Code

~~~bash
claude mcp add --transport http auren https://mcp.example.com/mcp
claude mcp list
~~~

Claude Desktop chat and Claude Code are distinct. Use the Code experience or Claude Code host for repository workflows; ordinary chat does not become a local repository agent merely because the MCP endpoint is connected.

### Cursor

Add to .cursor/mcp.json or ~/.cursor/mcp.json:

~~~json
{
  "mcpServers": {
    "auren": {
      "url": "https://mcp.example.com/mcp"
    }
  }
}
~~~

Cursor supports remote Streamable HTTP and OAuth discovery through the protected-resource metadata. Complete its sign-in flow when prompted. If using a pre-registered static OAuth client, configure its client ID through Cursor's auth settings; do not commit client secrets.

### Gemini CLI

Add to the Gemini CLI MCP settings:

~~~json
{
  "mcpServers": {
    "auren": {
      "httpUrl": "https://mcp.example.com/mcp",
      "timeout": 30000,
      "trust": false
    }
  }
}
~~~

Use Gemini's remote MCP OAuth support with the configured issuer. Gemini CLI can also register the endpoint directly:

~~~bash
gemini mcp add --transport http auren https://mcp.example.com/mcp
gemini mcp list
~~~

For Gemini Code Assist Agent mode in VS Code, add the server to the Gemini settings JSON file (usually ~/.gemini/settings.json):

~~~json
{
  "mcpServers": {
    "auren": {
      "httpUrl": "https://mcp.example.com/mcp",
      "timeout": 30000,
      "trust": false
    }
  }
}
~~~

Gemini Code Assist Agent mode uses the Gemini CLI tool integration for MCP. It is a separate acceptance target from the standalone Gemini CLI. As of June 18, 2026, Gemini CLI and Gemini Code Assist IDE extensions stopped serving Gemini Code Assist for Individuals, Google AI Pro and Google AI Ultra tiers. Gemini Code Assist Standard/Enterprise and paid Gemini API-key access remain available; affected consumer users should use Antigravity CLI/IDE instead.

### Antigravity CLI and IDE

Antigravity supports remote Streamable HTTP MCP. Its remote configuration uses serverUrl, not the legacy httpUrl field:

~~~json
{
  "mcpServers": {
    "auren": {
      "serverUrl": "https://mcp.example.com/mcp",
      "timeoutSeconds": 600
    }
  }
}
~~~

For Antigravity CLI, place this in ~/.gemini/config/mcp_config.json (global) or .agents/mcp_config.json (workspace). Open the interactive manager with /mcp to verify the connection. For Antigravity IDE, add the same server through the MCP Servers settings or its mcp_config.json.

### VS Code with GitHub Copilot Agent mode

Add to .vscode/mcp.json:

~~~json
{
  "servers": {
    "auren": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
~~~

The VS Code MCP host is not the same as Microsoft Visual Studio. Visual Studio itself needs a separate product/version acceptance test before it can be marked supported.

### Microsoft Visual Studio 2022 / 2026

Visual Studio 2022 version 17.14 or later and Visual Studio 2026 support remote MCP through GitHub Copilot Agent mode. Add a solution-level .mcp.json:

~~~json
{
  "servers": {
    "auren": {
      "url": "https://mcp.example.com/mcp"
    }
  }
}
~~~

Open Copilot Chat in Agent mode, add or enable the AUREN server in the tool picker, and complete the OAuth sign-in prompt. The AUREN service runs on its configured host; this does not give it unrestricted access to the Visual Studio workstation.

Claude Code's supported IDE integrations are separate from Microsoft Visual Studio. Use Claude Code CLI, Claude Code for VS Code, or Claude Desktop remote connectors for Anthropic-hosted workflows. Visual Studio can connect to AUREN through its own MCP host and Copilot Agent mode.

### ChatGPT web and ChatGPT desktop

Use the custom MCP app/connector setup in the ChatGPT workspace and point it at https://mcp.example.com/mcp. Complete the OAuth connection using the configured identity provider. Workspace admin settings, plan eligibility and app/developer-mode availability are controlled by ChatGPT, not by AUREN.

The ChatGPT OAuth authorization server must support authorization-code flow with PKCE S256, the resource parameter and either Client ID Metadata Documents (CIMD) or Dynamic Client Registration (DCR). ChatGPT does not use machine-to-machine client-credentials grants or customer-supplied API keys for this flow. AUREN publishes protected-resource metadata and validates access tokens, but it does not implement the authorization server or client registration. ChatGPT also presents an OpenAI-managed client certificate; if client identity verification is required, enforce the current OpenAI certificate chain/SAN policy at the TLS ingress. Do not assume a public endpoint alone proves the caller is ChatGPT.

Full MCP write/modify access is currently limited to eligible Business, Enterprise and Edu workspaces. Pro users can connect custom MCP servers with read/fetch permissions, while Free does not have the full custom MCP write path. Codex app, CLI and IDE extension share MCP host configuration. ChatGPT desktop is a separate product surface and is not covered by Codex settings; treat it as unverified until a dedicated connection path is tested. ChatGPT web's custom MCP flow is documented; mobile custom MCP is not supported.

### Groq, xAI/Grok and other model APIs

These are model-provider APIs, not interchangeable MCP host applications. AUREN now includes direct API adapters for Anthropic, OpenAI, Gemini, Groq, xAI, OpenRouter, Together, Fireworks, DeepSeek, Mistral, Cerebras, Perplexity and allowlisted custom OpenAI-compatible endpoints. The adapters are integrated with HybridModelRouter and use explicit provider order, per-provider secrets and shared execution budgets. They are repository-tested but not live-verified with vendor credentials; see [Model Provider Integration](MODEL_PROVIDER_INTEGRATION.md).

## Official client references

- [ChatGPT custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [OpenAI MCP authentication and client identification](https://developers.openai.com/plugins/build/auth)
- [OpenAI MCP server integration guide](https://developers.openai.com/api/docs/mcp)
- [Claude remote MCP connectors](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp)
- [Claude Desktop remote connectors](https://support.anthropic.com/en/articles/11175166-about-custom-integrations-using-remote-mcp)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp/)
- [Gemini Code Assist Agent mode and MCP](https://docs.cloud.google.com/gemini/docs/codeassist/use-agentic-chat-pair-programmer)
- [Google transition from Gemini CLI to Antigravity CLI](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)
- [Antigravity MCP configuration](https://antigravity.google/docs/mcp)
- [Visual Studio MCP servers](https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=visualstudio)

## Read-only deployment acceptance

After deploying the service behind TLS and configuring the external OAuth authorization server, run the live acceptance probe with a short-lived read-only access token. The probe verifies HTTPS/host allowlisting, health, protected-resource metadata, modern `server/discover`, modern cacheable `tools/list`, legacy `initialize` fallback and the minimum read-only operator tools. It does not submit tasks or make approval/admission changes.

```bash
export AUREN_MCP_ENDPOINT=https://mcp.example.com/mcp
export AUREN_MCP_ALLOWED_HOSTS=mcp.example.com
export AUREN_MCP_ACCESS_TOKEN="<short-lived-read-only-token>"
python scripts/remote_mcp_acceptance.py
```

The token is read from the environment and is never printed. Use a principal with read, status and evidence scopes only. A successful probe proves both MCP protocol eras against the deployed endpoint; it does not replace vendor-specific ChatGPT/Codex/Claude/Gemini/IDE acceptance, load testing, OAuth lifecycle tests or independent security review.

## Production boundary and remaining gates

This implementation provides an authenticated single-host operator service. It is not yet a multi-host, highly available deployment:

- Trigger, execution and control state currently rely on AUREN's SQLite-backed durable stores. Keep one service replica on persistent local disk; do not place SQLite WAL databases on NFS or treat a shared network filesystem as a distributed database.
- Multi-host active-active operation requires deployment PostgreSQL-backed trigger, task, control and rate-limit stores, plus host identity/failover evidence. Do not scale replicas across physical hosts until that work is independently verified.
- OAuth authorization-server setup, DCR/CIMD, PKCE, refresh/revocation and client registration must be configured and tested with the selected identity provider.
- ChatGPT's optional client mTLS identity check is not terminated by this ASGI app. If required, enforce it at the TLS ingress using the currently published OpenAI certificate chain and SAN policy.
- Provider-specific desktop/IDE acceptance remains necessary. MCP protocol support does not prove the host's OAuth UX, tool confirmations, workspace scope or cancellation behavior.
- Independent security review, load testing, backup/restore, secret rotation, disaster recovery and live vendor-client tests remain release gates.

## Verification

Run from the repository root:

~~~bash
python -m unittest tests.test_remote_mcp_service tests.test_remote_mcp_acceptance -v
python -m unittest tests.test_chat_capability_mcp tests.test_provider_conformance tests.test_remote_model_providers -v
python -m unittest discover -s tests -p '*trigger_runtime*' -v
~~~

After deployment, test the endpoint with MCP Inspector and every target host. Verify initialization, protocol negotiation, tool discovery before OAuth, the OAuth linking flow, scopes, tool errors, idempotency, task status, evidence, cancellation, approval authorization, readiness, rate limiting, revocation and restart recovery.
