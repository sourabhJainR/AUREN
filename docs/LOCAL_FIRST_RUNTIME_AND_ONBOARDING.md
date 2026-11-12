# Local-first Runtime and Repository Onboarding Architecture

**Status:** Architecture and acceptance register; implementation status reconciled against main on 2026-11-12.  
**Product direction:** Local-first by default. Single-user local mode is the default profile. Managed runtime integration is optional and explicit. Remote MCP and local OAuth are separate optional choices.

### Runtime profiles

| Profile | Default | Runtime factory | Identity / transport |
|---|---:|---|---|
| Single-user local | Yes | Not used or required | Local caller identity/token; loopback MCP only |
| Managed | No; explicit opt-in | Optional advanced trusted extension | Host-managed identity, shared policy/budgets and deployment services as configured |

The user can switch profiles with `auren mode --mode managed --apply` or `auren mode --mode single_user_local --apply`. An advanced factory can be named only while opting into managed mode: `auren mode --mode managed --runtime-factory module:function --apply`. Switching back to local mode clears the factory reference. Local setup must never prompt for, discover, or import a project runtime factory.

The profile selector, built-in single-user composition root and local MCP startup are implemented on main. The factory-backed host is an advanced managed-mode extension only.

### Implementation status (2026-11-12)

**Implemented**
- `auren init`: bounded, read-only repository discovery; creates only `.auren/project.json`; refuses overwrite and symlinked state roots.
- `auren doctor`: reports runtime prerequisites, installed provider CLIs, configured direct API providers without exposing secrets, local Ollama-compatible endpoint/model discovery, and remote-inference policy. Presence checks are not live provider validation.
- `auren up`: one-command first-run bootstrap, canonical local runtime startup, per-client best-effort configuration and browser console launch. Existing project profiles are validated and never overwritten.
- `auren connect`: previews by default; with `--apply`, backs up and atomically updates supported local JSON and Codex TOML MCP client configuration while preserving unrelated entries.
- `auren providers`: shows detected CLI providers, configured API routes, local model readiness and privacy routing without exposing credentials.
- Single-user local mode: factory-free composition root with canonical controller, durable plan/recovery stores, Guard, local operator identity, assignment authorization, durable approvals, persisted budgets and supervisor.
- Local MCP and dashboard share the canonical runtime and bind to loopback. Direct API inference is explicit opt-in and proposal-only for public data.
- Ordered direct API provider fallback and per-request provider subsets are implemented with bounded supervisor attempt/deadline controls.

**Still blocked / not implemented**
- Scoped repository coding-agent executor with workspace/process isolation, cancellation, output bounds and restart reconciliation. The built-in local MCP task handler remains proposal-only.
- Automatic provider invoker registration for every graph/crew role, per-role model preferences and persisted per-user provider selection.
- Parallel multi-model voting/ensemble execution and provider-native cost accounting.
- Live handshake and end-to-end acceptance for every listed IDE, desktop client and cloud host; local auto-configuration covers supported JSON hosts and Codex TOML only.
- Cross-platform user-service registration, secure Windows credential ACL validation, macOS Keychain integration, and clean-machine upgrade/rollback/uninstall acceptance.
- Live vendor credentials/model conformance, provider licensing/privacy acceptance and independent production deployment evidence.

Do not treat a configured key/model pair, detected CLI or mocked adapter test as live provider acceptance. Local MCP remains proposal-only until the scoped coding executor passes its own security and recovery gates.

## Objective

A developer should be able to install AUREN on a new workstation, open a new or existing repository, connect a supported local AI client, and begin a verified engineering task without writing a runtime factory or assembling AUREN internals.

AUREN owns the runtime composition and execution authority. Providers supply model capability. MCP clients supply an interaction surface. A project profile describes repository-specific context and preferences; it does not grant authority.

## Required composition

The managed composition root must create and validate, in dependency order:

1. A versioned, reviewed, non-empty engineering graph with explicit planner, implementer, verifier and independent reviewer roles. Missing adapters must be represented as unavailable capabilities, not no-op nodes.
2. The canonical `AdaptiveRuntime` and supervisor-owned `ExecutionController`.
3. Durable trigger, task, checkpoint, progress and evidence stores with restart reconciliation.
4. A workspace-bound filesystem and process boundary; no implicit access outside the selected repository.
5. Guard policy, caller identity, operation authorization, approval resolution and resource budgets.
6. Provider invoker registry with explicit capabilities, privacy eligibility, model IDs, credentials, deadlines and output limits.
7. One lifecycle owner shared by CLI, dashboard, local MCP and optional background service.

The runtime factory contract remains available only as an advanced trusted extension. It must pass the same validation as the built-in composition root.

## Startup invariants

Startup is transactional from the user's perspective:

- Validate runtime version, state schema, workspace identity and graph digest before binding endpoints.
- Validate that the graph is non-empty and every required stage has an eligible reviewed adapter.
- Validate the execution controller, durable trigger runtime, journal and evidence dependencies.
- Validate configured provider invokers without printing or persisting credentials in project state.
- Bind local HTTP only to loopback, generate or load a private local credential, and verify dashboard and MCP readiness.
- Report `working`, `blocked` or `failed` with the failing dependency and next action. Never report ready after a partial startup.
- On shutdown, stop accepting new work, persist stop intent, allow bounded graceful shutdown, and leave resumable work in durable state.

No startup shortcut may use an empty graph, permissive default authorizer, no-op provider, in-memory trigger store, unscoped workspace or unbounded retry.

## Local MCP and authentication

The local endpoint is the default MCP transport for supported local clients. Bind to `127.0.0.1` and/or `::1` only. Keep remote binding behind a separate explicit deployment command and operator configuration.

Authentication modes:

- **Local default:** private per-user bearer credential or supported OS-backed caller identity; no browser OAuth ceremony required.
- **Local secured:** optional stronger identity provider or OS credential integration with revocation and scoped roles.
- **Remote:** explicit opt-in, HTTPS ingress, OAuth 2.1 authorization-code flow with PKCE, audience/resource validation, revocation, audit and deployment acceptance.

Authorization is mandatory in all modes. Each tool invocation is checked against caller, project, operation, data scope, policy, approval, rate and execution budgets. Client confirmation UI is supplementary and never replaces server-side authorization.

The local MCP service, stdio bridge, dashboard and CLI must call the same runtime instance and canonical durable stores. No shadow queue or second task authority is permitted.

## Repository onboarding

### Existing repository

The initial scan is read-only and bounded. Capture:

- canonical path, Git root, branch, HEAD and dirty status;
- repository instructions and existing AUREN configuration;
- language/framework/dependency manifests;
- build, lint, test and type-check commands where discoverable;
- CI workflows, test coverage and deployment/release configuration;
- supported local AI clients and existing MCP registrations.

Produce a readiness report with confidence and unknowns. Never run discovered scripts during inspection. Never overwrite existing instructions, hooks, workflows, credentials or MCP entries. Any proposed change must be shown as a diff, backed up, explicitly approved, applied atomically and verified. Project state belongs under `.auren/`; machine-wide secrets and runtime state belong under the user's AUREN home.

### New repository

Create only the minimal project profile, ignore rules if approved, and optional starter engineering plan. Ask before choosing application architecture, language, framework, license, deployment target or release policy. Do not scaffold arbitrary application code as a side effect of onboarding.

## Client auto-configuration

A client adapter is supported only when its configuration schema and discovery path are version-pinned and tested.

- Detect installed clients without assuming they are authenticated.
- Read and parse existing configuration before proposing a change.
- Preserve all unrelated servers, settings, comments where the format permits, and user-owned credentials.
- Back up the exact prior bytes and write atomically.
- Add only the AUREN local MCP entry, including the stdio proxy where required.
- Validate syntax, launch/handshake, tool discovery, workspace identity and a read-only evidence/status request.
- Provide a clear manual configuration fallback when a client is unsupported or configuration is ambiguous.
- Never write a loopback URL into a cloud-hosted client and claim it will work.

Client categories include Codex CLI/app, Claude Code/Desktop, Gemini CLI/Antigravity, VS Code Copilot, Microsoft Visual Studio, Cursor and other MCP hosts. ChatGPT web and hosted Claude require remote MCP; they are not local loopback clients.

## Provider onboarding

MCP client integration and model API integration are separate concerns. AUREN must not assume that a user's ChatGPT/Claude/Copilot login exposes reusable API credentials.

Offer three explicit choices:

1. Use a locally installed agent CLI through a reviewed subprocess adapter with fixed executable, environment allowlist, working directory, timeout, output cap and cancellation.
2. Use a configured local model endpoint with explicit host allowlist and privacy settings.
3. Configure one or more direct model API providers through a secure credential store.

Provider capability is per role and operation. Routing must honor user order, provider allowlists, privacy classification, cost/time budgets, cancellation and no-fallback rules. Unknown or unavailable providers fail visibly; they are not silently replaced.

## Built-in local MCP composition

The single-user local host now constructs its own runtime; it does not import or discover a project runtime factory. The composition installs the canonical AdaptiveRuntime, ExecutionController, durable trigger runtime, engineering-plan store/recovery bridge, trusted plan-dispatch root, continuous supervisor, host-owned local operator identity, assignment authorizer, durable approval store and persisted execution-budget resolver before MCP starts accepting tasks.

Local MCP task submission is routed through that host composition. It captures the Git HEAD, Guard policy digest and selected provider/model at admission, then dispatches a role-assigned plan task through the trusted plan-dispatch root and canonical controller. The continuous supervisor claims and resumes queued controller work. Provider operation receipts are persisted before/after invocation; an interrupted ambiguous provider call is not repeated automatically.

The local MCP provider path uses an admission-bound Ollama-compatible model for private work when one is available. If no capable local model is present, a configured direct API provider may be used only when remote inference is explicitly enabled and the workspace is classified as public. Provider identity and model are fixed at admission; a failed local route does not fall back to a remote API. The path remains proposal-only: it receives task text, has no repository tools, and cannot modify files, merge, release or deploy. It is not equivalent to a full coding-agent executor.

Remote inference is disabled by default. To enable it, the operator must explicitly configure a provider and declare that the submitted work is public:

```bash
export AUREN_REMOTE_PROVIDER_ORDER=openai
export OPENAI_API_KEY="<injected-from-a-secret-manager>"
export OPENAI_MODEL="your-approved-model-id"
export AUREN_LOCAL_REMOTE_INFERENCE=enabled
export AUREN_LOCAL_DATA_CLASSIFICATION=public
```

Use the provider-specific key/model names documented in `MODEL_PROVIDER_INTEGRATION.md`. Do not place credentials in project files, MCP arguments or task context. If the public-data declaration or remote-inference opt-in is absent, local MCP starts but task admission is denied before a durable task is queued. Provider identity, model, source revision and Guard-policy digest are bound to the accepted task; changing them requires a new task identity.

Local coding-agent CLI invocation is not enabled by this composition yet. Detection/configuration of Codex, Claude Code and Gemini CLI is not treated as execution readiness. They remain blocked until a reviewed scoped subprocess/workspace executor can enforce assignment paths, environment allowlists, process cancellation, output limits and recovery semantics. A local CLI must never be launched as an unscoped subprocess merely because it is installed.

## State ownership and storage

- Machine runtime, provider references, local MCP credential and service metadata: user-owned AUREN home.
- Project profile and approved repository-specific settings: repository `.auren/`.
- Tasks, checkpoints, trigger state, progress journal, side-effect ledger and evidence: existing canonical stores.
- Client settings: client-owned config; changed only by an explicit `auren up` or `auren connect --apply` action, with backups and per-client failure isolation.
- Remote deployment state: deployment-owned durable stores. SQLite remains single-host only; do not claim multi-host readiness until PostgreSQL admission, host identity and failover evidence pass.

## Cross-platform lifecycle

- Windows: user-scoped install, secure credential ACLs, process lifecycle and optional scheduled/user service; no admin rights unless explicitly needed.
- macOS: user-scoped install, Keychain-compatible credential path, optional LaunchAgent.
- Linux: user-scoped install, owner-only state and optional systemd user service.
- Every platform supports start/status/stop, restart recovery, upgrade, rollback and uninstall without deleting user data unless explicitly requested.
- Service registration is opt-in and reversible. A foreground process remains a supported fallback.

## Acceptance gates

| Gate | Required proof |
|---|---|
| Managed runtime | Fresh install starts without a project runtime factory; graph is non-empty, reviewed and digest-bound; controller and durable trigger runtime are present |
| Provider readiness | At least one explicitly configured, live-conformant invoker for the selected execution path; no fake or no-op provider |
| Local MCP | Loopback-only; local OAuth disabled and enabled paths tested; authorization and workspace isolation enforced |
| Recovery | Process kill/restart at enqueue, claim, dispatch, checkpoint, receipt and terminal commit boundaries; no duplicate external effects |
| Onboarding | New and existing repo scan; dirty Git tree preserved; no unapproved file edits; profile deterministic and reversible |
| Client configuration | Existing config backup/restore, merge preservation, syntax validation, handshake, tool discovery and read-only probe for each supported client/version |
| Cross-platform | Clean-machine Windows, macOS and Linux install, upgrade, rollback, uninstall and restart tests |
| Remote option | Disabled by default; HTTPS/OAuth configuration and independent remote acceptance when explicitly enabled |
| Security | Threat review for loopback origins, DNS rebinding, credential files, symlinks, process environment, workspace escape, client config injection and tool authorization |
| Release | Required checks pass on exact PR head; post-merge checks pass on main SHA; release artifact provenance and rollback are verified |

A repository unit test or mocked provider test is not live vendor acceptance. A passing local MCP handshake is not proof of production security or multi-host availability.

## Delivery order

1. Managed runtime composition root and startup/doctor contract.
2. Local MCP lifecycle using the managed runtime and optional local authentication.
3. Safe new/existing repository onboarding.
4. Client detection and transactional auto-configuration.
5. Provider selection and live conformance.
6. Windows/macOS/Linux installer and user-service lifecycle.
7. End-to-end acceptance on clean machines and representative repositories.
8. Optional remote MCP deployment path and independent production review.

Each phase must ship with focused tests and must not weaken existing authority, approval, budget, evidence, recovery, privacy or merge gates.
