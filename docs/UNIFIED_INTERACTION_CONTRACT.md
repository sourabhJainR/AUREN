# AUREN Unified Interaction Contract

## Goal

AUREN should feel like one engineering system whether a user enters through an LLM chat host, an IDE, a CLI or the desktop console. The host is an interaction surface. AUREN remains the owner of task identity, execution, policy, approvals, recovery and evidence.

## Shared task lifecycle

1. **Submit:** create a task with a caller-scoped idempotency key. A successful submission returns an acceptance receipt, not a completion claim.
2. **Track:** read the same task state and progress journal. The visible lifecycle is pending, running, waiting, blocked, recovering, completed, failed or cancelled.
3. **Approve:** list and decide durable approval requests through the authenticated host. Client confirmation UI does not replace server-side authorization.
4. **Cancel:** request cancellation. Active work remains nonterminal until the supervisor confirms the outcome.
5. **Verify:** retrieve evidence, source revision, policy digest and terminal receipt before reporting completion.

## Surface mapping

| Surface | Primary interaction | Shared source of truth |
|---|---|---|
| Chat / IDE / desktop MCP host | `auren.submit_task`, `auren.list_tasks`, `auren.get_task`, `auren.cancel_task`, `auren.get_evidence`, approval tools | Host-owned ExecutionController, durable trigger store, Guard, approval store and progress journal |
| Local CLI | `auren task submit/list/status/cancel/evidence/approvals/decide-approval`, `auren progress --json`, `auren continuous status`, `auren providers` | The same authenticated local MCP service, task controller and project-scoped progress journal |
| Python SDK | `AurenClient.submit`, `run_once`, `task`, `progress` and trusted plan dispatch | In-process facade over `ExecutionController`; inject the host-owned controller to share the same runtime object. For another process, use the local or remote MCP API rather than creating a parallel controller |
| Desktop console | Session Control Center, task progress, evidence and delivery health | The canonical local runtime and progress journal |
| Hosted chat | Authenticated remote Streamable HTTP MCP | Deployment-owned controller and durable stores |

## Provider-neutral model path

MCP host connectivity and model inference are separate. A chat host may use its own model to call AUREN tools; that does not grant AUREN access to the host's API credentials. AUREN can also route eligible inference through its configured local model or direct API provider chain. Provider order is explicit, each attempt is budgeted, and private or restricted work remains local-only.

Use `auren providers` to inspect detected providers and current configuration. A configured provider is not live-conformance evidence. Do not place keys in project profiles, MCP arguments or task payloads.

## Local and hosted boundaries

- `auren up` is the one-command local entry point. It validates or creates the project profile, starts the shared local runtime, configures supported local clients with backups and opens the desktop console.
- Local endpoints bind to loopback and use a private bearer credential. They are not reachable from hosted chat services.
- ChatGPT web, hosted Claude and other cloud clients require a separately deployed HTTPS remote MCP endpoint and OAuth 2.1 authorization server. Deployment and multi-host readiness remain independent acceptance gates.
- AUREN never silently changes a cloud endpoint to a local URL, forwards secrets to a model provider, or substitutes a provider when privacy policy forbids the route.

## Evidence boundary

A common interface does not imply common trust. Every operation is authorized by AUREN using the authenticated principal, workspace, operation scope, Guard policy, approval state and finite budgets. A host's own permissions or model-side confirmation cannot bypass these checks.

CI tests can prove local contracts and mocked integration behavior. They do not prove live vendor login, provider licensing, external OAuth deployment, multi-host failover or independent security review.
