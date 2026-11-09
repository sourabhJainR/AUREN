# Local-first Runtime and Repository Onboarding Architecture

**Status:** Architecture initiative; implementation and acceptance are tracked independently.  
**Product direction:** Local-first by default. Remote MCP is optional. Local OAuth is optional.

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

## State ownership and storage

- Machine runtime, provider references, local MCP credential and service metadata: user-owned AUREN home.
- Project profile and approved repository-specific settings: repository `.auren/`.
- Tasks, checkpoints, trigger state, progress journal, side-effect ledger and evidence: existing canonical stores.
- Client settings: client-owned config, modified only by an explicit connect action with backup.
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
