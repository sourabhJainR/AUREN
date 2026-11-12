# Use AUREN from ChatGPT web

**Status:** setup guide and readiness boundary. This document is not evidence that AUREN has been deployed or connected to a ChatGPT workspace.

## What works today

AUREN has an authenticated remote MCP service at the protocol level. It exposes bounded operator tools for service health, task submission and status, cancellation, evidence, approvals, and admission control. The endpoint uses Streamable HTTP at `/mcp`, protected-resource metadata, scoped authentication, and an external OAuth token-introspection service.

The remote launcher currently requires a trusted deployment-owned runtime factory. The repository does not contain a live hosted AUREN endpoint, an OAuth tenant/client registration, or deployment credentials. The sample host name `mcp.example.com` is a placeholder.

The built-in single-user local composition is factory-free, but its model task handler is proposal-only. It cannot edit repository files, run builds, merge, release, or deploy. A successful MCP connection or accepted task receipt does not mean the engineering task completed. The scoped repository coding executor and its isolation/recovery acceptance remain open.

## ChatGPT plan and workspace access

As checked against OpenAI's current ChatGPT custom-MCP documentation on 2026-11-13:

- **Free:** custom MCP server connections are not currently available.
- **Pro:** custom MCP connections are limited to read/fetch permissions.
- **Business, Enterprise, and Edu:** full MCP, including write/modify actions, is available subject to workspace permissions and admin controls.

For Business workspaces, admins/owners create and publish custom MCP apps. For Enterprise/Edu, admins/owners can create and test, authorized developers may be granted creation access, and admins/owners publish. Workspace policy can further restrict access and individual actions.

OpenAI controls plan eligibility and workspace permissions. AUREN cannot enable these capabilities from its repository.

## Required deployment before connecting

Choose one deployment owner and keep the first deployment single-host:

1. Deploy AUREN's remote MCP service behind HTTPS. Keep the ASGI process on loopback behind a TLS ingress; expose only the ingress.
2. Provide a trusted runtime factory that returns the canonical AUREN runtime with a non-empty reviewed graph, execution controller, and durable trigger runtime. Do not use a placeholder or no-op graph.
3. Configure an external OAuth authorization server. It must support authorization-code flow with PKCE S256 and the client registration method required by the ChatGPT connection (CIMD or DCR). Configure refresh-token issuance, issuer and resource/audience validation. AUREN is an OAuth resource server; it is not the authorization server.
4. Configure the OAuth introspection endpoint, client credentials, issuer, audience, exact MCP resource URL, allowed host/origin values, and least-privilege scopes through the deployment's secret manager. Never commit secrets or put them in project profiles.
5. Use persistent local disk for the current SQLite-backed task and operator-control stores. Run one service replica. Do not use NFS/shared SQLite or claim multi-host high availability; multi-host admission and control stores need separate PostgreSQL and host-failover acceptance.
6. Publish the deployment-specific privacy, retention, backup, recovery, and security terms before exposing real repository data.
7. Configure a repository workspace on the AUREN host. The hosted ChatGPT client cannot reach a developer machine's loopback address or access its local filesystem.

Follow [Remote MCP Operations](REMOTE_MCP_OPERATIONS.md) for the exact environment contract and deployment command. Follow [Remote MCP Privacy](REMOTE_MCP_PRIVACY.md) before exposing the service.

### Deployment command shape

The remote launcher requires a trusted factory; the exact module path and environment values belong to the deployment owner:

```bash
python -m portable.remote_mcp_server \
  --project-root "$AUREN_PROJECT_ROOT" \
  --runtime-factory "$AUREN_MCP_RUNTIME_FACTORY" \
  --host 127.0.0.1 \
  --port 8765
```

Do not bind the service publicly from the Python process. Terminate TLS and enforce ingress controls at the deployment boundary.

## Interim use from ChatGPT web without remote MCP

If the ChatGPT workspace has the GitHub app connected, you can use the repository's AUREN engineering contract before the remote MCP deployment is ready. Select the GitHub app for the conversation and ask ChatGPT to inspect `AGENTS.md`, `skills/ai-coding-orchestrator/SKILL.md`, and the relevant architecture/acceptance documents before changing code.

Reusable task prompt:

```text
Use sourabhJainR/AUREN as the repository source of truth. Read AGENTS.md and
skills/ai-coding-orchestrator/SKILL.md first, then inspect the current main SHA,
open issues/PRs, relevant implementation and tests. Follow AUREN's canonical
plan, Guard, budget, evidence, review, and exact-head CI rules. Make the
smallest safe change, preserve unrelated files, and report the exact commit/PR,
checks, evidence, blockers, and next safe action. Do not claim that the AUREN
runtime executed a task unless a real AUREN runtime receipt proves it.
```

This is an instruction-guided GitHub workflow, not a live AUREN MCP session. It does not start AUREN's supervisor, use its durable task stores, or prove that the scoped coding executor is active. Do not treat it as equivalent to the hosted MCP integration.

## Connect it in ChatGPT web

After the endpoint and OAuth provider are live, and the ChatGPT workspace permits custom MCP apps:

1. Open ChatGPT on the web and go to **Plugins**.
2. Select **+**, then **Add custom MCP server**.
3. Name the connection **AUREN**.
4. Enter the deployed HTTPS endpoint, for example `https://<your-host>/mcp`. Do not use `127.0.0.1`, `localhost`, or the example domain.
5. Select OAuth and complete the identity-provider sign-in.
6. Review the discovered tool list and risk warning. Create the plugin/app, then install it.
7. Start a new conversation, type `@`, and select **AUREN** for the message that should use its tools.

The current ChatGPT UI and workspace policy may differ. See [OpenAI's current connection guide](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## Start with read-only access

For the first connection, grant only the read scopes and enable only:

- `auren.get_profile`
- `auren.service_status`
- `auren.list_tasks`
- `auren.get_task`
- `auren.get_evidence`
- `auren.list_approvals`
- `auren.get_approval`

Verify that the workspace identity, service health, task visibility, and evidence summaries are correct. Do not enable task submission, cancellation, approval decisions, provider resume, or admission controls until the deployment's operator identity, authorization, approval policy, and audit behavior have been reviewed.

Even when ChatGPT displays a confirmation prompt, AUREN must enforce authorization and approval on the server. ChatGPT confirmation is not a replacement for Guard.

## What is still needed for real software development

AUREN's current local model path can return a proposal, not a repository patch. To use AUREN as the actual engineering executor from ChatGPT web, the deployment must additionally pass the scoped coding-agent executor gate:

- workspace-bound filesystem and process isolation;
- exact assignment/path scope and denial before process launch;
- environment and credential allowlists;
- bounded output, deadline propagation, cancellation, and process-tree cleanup;
- durable operation receipts and restart reconciliation without replaying uncertain effects;
- tests for workspace escape, secret access, unauthorized egress, process failure, and recovery;
- independent review and exact-head CI, followed by deployed host acceptance.

Until those checks pass, use ChatGPT web only for read-only AUREN status/evidence or proposal workflows. Do not present an accepted proposal as an implemented change.

## Verify the deployment

Run the repository's read-only remote acceptance probe with a short-lived read-only token:

```bash
export AUREN_MCP_ENDPOINT="https://<your-host>/mcp"
export AUREN_MCP_ALLOWED_HOSTS="<your-host>"
export AUREN_MCP_ACCESS_TOKEN="<short-lived-read-only-token>"
python scripts/remote_mcp_acceptance.py
```

Then complete the real ChatGPT OAuth, tool-discovery, and read-only smoke tests. The probe does not prove ChatGPT account eligibility, OAuth refresh/revocation, production isolation, live repository execution, or multi-host readiness.

For future changes to the MCP tool list, refresh the app's tool definitions in ChatGPT and review the diff. ChatGPT workspace apps use a reviewed tool snapshot; server-side changes are not automatically enabled.
