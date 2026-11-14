# AUREN Chat, CLI and Desktop Integration: Production Readiness Review

**Review baseline:** `main` at `2134566b997b91dd01d7100df6b50ad4abc67786`  
**Review scope:** ChatGPT web/desktop, Codex CLI/app, AUREN CLI, and the AUREN local desktop console.  
**Verdict:** The local integration foundation is implemented, but the end-to-end product integration is **not yet production-ready**.

This review distinguishes repository implementation from a deployed service and from a real vendor-client acceptance test. Documentation, mocked tests, and successful MCP protocol tests do not prove that a user can sign in and complete a governed task in a live desktop or hosted client.

## 1. Current status

| Surface | What exists | What is still missing | Status |
|---|---|---|---|
| AUREN CLI | `auren up`, `doctor`, `task`, `progress`, `continuous`, `providers`, integration inventory and local MCP probe | Clean-install and upgrade acceptance on supported Windows/macOS/Linux versions; real Codex client smoke test | Implemented; live acceptance pending |
| Local MCP | Loopback-only HTTP endpoint, owner-only token file, project binding, scoped tools, stdio bridge | Installed-client acceptance for each supported host and OS | Implemented; live acceptance pending |
| Codex CLI | Local configuration path and shared task API | Real authenticated discovery, read-only use, submit/approve/cancel/restart test | Documented/configurable; unverified |
| Codex app / IDE | Shared Codex MCP configuration and repository instructions/skills | App-specific UI discovery, OAuth, tool invocation and long-running task acceptance | Partial |
| ChatGPT web | Remote Streamable HTTP MCP resource server and OAuth introspection integration | Hosted endpoint, OAuth tenant/client registration, workspace app installation, live ChatGPT acceptance | Not deployed |
| ChatGPT desktop | Host-specific MCP setup is described in compatibility docs | Exact supported configuration path and real desktop-client acceptance; do not infer it from Codex CLI success | Unverified |
| AUREN desktop console | Local browser-based Engineering Console / Session Control Center, health and read-only runtime views | It is not a native desktop application; no signed native installer, auto-update or desktop lifecycle integration | Browser console only |
| Remote deployment | ASGI service, scoped operator tools, health/readiness, OAuth resource-server validation | Deployment ownership, TLS/ingress, secrets, backups/restore, retention, security review, monitoring and operational runbooks | Reference implementation only |

## 2. What is already implemented

- The CLI and local MCP service share the canonical AUREN task lifecycle rather than maintaining separate task stores.
- Local service defaults are loopback-only. The local bearer token is stored separately with restrictive permissions.
- Task submission, status, cancellation, evidence and approvals are exposed through the local MCP interface and CLI.
- The local dashboard is an observation surface, not a second execution authority.
- The remote MCP service has scoped tools, OAuth token introspection, project binding, request limits and explicit approval boundaries.
- The repository includes host configuration examples and compatibility guidance.

Relevant implementation entry points:
- `auren_cli.py`
- `portable/local_host.py`
- `portable/mcp_local_client.py`
- `portable/mcp_stdio_proxy.py`
- `portable/remote_mcp.py`
- `portable/remote_mcp_server.py`
- `portable/onboarding.py`
- `docs/UNIFIED_INTERACTION_CONTRACT.md`

## 3. Production blockers

### P0 — ChatGPT web is not connected to a live AUREN service

ChatGPT web cannot access a workstation's loopback MCP endpoint. The repository explicitly has no deployed hosted endpoint or OAuth tenant. AUREN therefore cannot claim that ChatGPT web integration is complete.

Required before release:
1. Deploy the remote service behind HTTPS with exact host and origin allowlists.
2. Configure an external OAuth 2.1 authorization server and validate issuer, audience, scopes, project claims, PKCE, refresh and revocation behavior.
3. Configure a ChatGPT workspace app with the correct read/write capabilities and least-privilege scopes.
4. Run real ChatGPT discovery and read-only acceptance, then a separately authorized task/approval/cancellation lifecycle test.
5. Store sanitized, versioned acceptance receipts. Never store bearer tokens or raw identity claims in evidence.

### P0 — Desktop wording currently mixes distinct products

The AUREN Engineering Console is a local web application opened in a browser. It is not a native desktop app. ChatGPT desktop, Codex app and the AUREN console are separate products and must have separate setup instructions and acceptance evidence.

Required decision:
- If "desktop app" means the AUREN console, document and test it as a local browser console.
- If a native AUREN desktop app is required, it needs a separately scoped desktop shell, signed installers, secure credential storage, lifecycle management and update strategy. None is present in the repository tree at this review baseline.

### P0 — Codex CLI/app acceptance is not proven

The local configuration writer and configuration examples are not equivalent to an installed Codex client test. Verify the actual installed Codex CLI/app version, configuration discovery, authentication, tool discovery and behavior after client restart. Test read-only calls first, then task submission, approval denial/approval, cancellation and recovery after an interrupted client connection.

### P1 — Cross-platform install and lifecycle acceptance

Run clean install, upgrade, rollback and uninstall/reinstall tests on supported Windows, macOS and Linux environments. Confirm:
- Paths with spaces and non-ASCII characters.
- Existing MCP configuration is backed up and preserved.
- Repeated `auren up` is idempotent.
- Partial startup failure closes the other service and reports a useful failure.
- Ctrl+C, SIGTERM and Windows process termination clean up child processes.
- Token and state permissions are correct on each OS.
- A stale endpoint or token cannot be mistaken for a healthy connection.

### P1 — Remote service operational readiness

The remote service currently uses SQLite-backed state and is single-host. Before public production use, complete backup/restore and recovery rehearsal, retention and deletion policy, secret rotation, alerting, rate-limit monitoring, independent security review and penetration testing. Do not claim multi-host HA with the current stores.

## 4. Required end-to-end acceptance matrix

Every result must include client version, OS, AUREN commit, MCP protocol version, auth mode, tool-set digest, test result and sanitized evidence reference.

| Scenario | Local CLI | Codex CLI/app | ChatGPT web | AUREN console |
|---|---:|---:|---:|---:|
| Clean install and startup | Required | — | — | Required |
| Read-only service/task/evidence discovery | Required | Required | Required | Required |
| Submit task and receive acceptance receipt | Required | Required | Required | UI must show same task |
| Track progress from a second surface | Required | Required | Required | Required |
| Unauthorized approval is denied | Required | Required | Required | Display only |
| Authorized approval is audited | Required | Required | Required | Display only |
| Cancellation reaches confirmed terminal state | Required | Required | Required | Display only |
| Client disconnect/restart and task recovery | Required | Required | Required | Refresh/reconnect |
| Wrong project / scope / replay is rejected | Required | Required | Required | Not applicable |
| Secret and prompt redaction | Required | Required | Required | Required |

A task is complete only when the canonical controller reports a terminal state and the evidence, source revision, policy digest and terminal receipt agree. A client acknowledgement or dashboard percentage is not completion evidence.

## 5. Release gate

Do not mark Chat, CLI and desktop integration production-ready until:
- P0 blockers above are closed.
- The acceptance matrix is executed against real clients, not only mocks.
- Exact-head CI passes for the release commit.
- Post-merge and post-deployment smoke tests pass.
- Security review and operational recovery evidence are attached.
- The compatibility matrix marks a host live-verified only when its receipt includes the exact client version and evidence reference.

## 6. Immediate implementation order

1. Keep the local CLI + local MCP + browser console as the supported single-user experience and prove it with clean-install E2E tests.
2. Prove Codex CLI and Codex app separately using the actual supported client versions.
3. Deploy and accept the remote MCP + OAuth path for ChatGPT web.
4. Decide whether a native AUREN desktop app is in scope. Do not label the browser console as a native desktop app.
5. Only then publish a production-ready claim for the corresponding surfaces.

**Bottom line:** AUREN has a credible shared integration architecture. The local CLI/MCP path is implemented; hosted ChatGPT remains undeployed; Codex and other vendor clients lack live acceptance evidence; and the AUREN desktop experience is currently a browser console, not a native desktop app. The honest release status is **integration foundation implemented, production acceptance incomplete**.
