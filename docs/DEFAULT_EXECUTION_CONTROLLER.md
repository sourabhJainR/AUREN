# AUREN Default Execution Controller

AdaptiveRuntime.run() is now the durable execution boundary for AUREN graph/team work.

## Default path

AdaptiveRuntime.run() -> ExecutionController -> AutonomousCompany -> existing Orchestrator(Graph).

This preserves the existing graph execution engine while making the supervisor the owner of task lifetime.

## Restart behavior

A session is mapped to a durable work unit. For a directly constructed `ExecutionController(root)`, its ledger is stored at `<root>/.auren/execution.sqlite3`. The default `AdaptiveRuntime` supplies `~/.auren/executive` as that root. On startup, `AurenStatePaths` copies known SQLite stores and session JSON from legacy `~/.auren` into matching canonical paths using atomic writes and SQLite backup. Legacy files are retained. If both roots contain conflicting data, startup fails closed with an actionable `StatePathConflict`; do not delete either copy before an explicit backup and reconciliation. See [issue #379](https://github.com/sourabhJainR/AUREN/issues/379) for migration rollout and live-environment verification.

If the worker exits after a work unit has started, the next invocation with the same session_id resumes the existing work unit rather than creating a new one.

Each task claim now advances a durable lease generation and receives a fresh fencing token. Heartbeats and terminal-state commits must match both values, so an expired/reclaimed worker cannot overwrite the newer owner's outcome. This does not undo a side effect already performed by a handler that outlives its lease; consequential handlers must use task-scoped idempotency keys at their external effect boundary. The current handler contract does not expose a supervised heartbeat callback, so handlers must finish within the lease window; longer operations need that callback added before they can safely rely on lease renewal.

The existing maintenance service calls resume_tasks() before its maintenance cycle. Its native service definitions already use restart-on-failure behavior, so host/process restart feeds back into the same durable queue.

## Durable handlers

Register an importable module-level handler with `runtime.register_task_handler(name, handler, execution_class=...)`. Every handler must be explicitly classified as `read_only`, `context_provider`, `ci_gated`, or `consequential`. Consequential handlers require both an operator-owned `promotion_gate_policy={"context_handler": "trusted-provider-name"}` and an exact-head `ci_gate_policy` with required checks and a separate trusted evidence provider. CI-gated handlers require exact-head CI. Context providers are internal-only and cannot be submitted as tasks. New and legacy-unclassified handlers fail closed until an operator reviews and explicitly classifies them. The class is persisted separately from task payload and cannot be downgraded by re-registration. Submit ordinary handlers with `runtime.submit_task(name, payload)` and resume with `runtime.resume_tasks()`. The context provider receives task identity only, not task payload, and must resolve repository/head/approver/canonical evidence from trusted live sources.

Handlers are stored as module:qualname, not serialized Python objects. This makes them reconstructable by a new process.

Each classified durable registration also stores a SHA-256 digest of the handler source. The controller recomputes it whenever the handler is loaded and refuses to run changed source under the same registered identity. This is an integrity check, not a signature: deployment provenance and OS-level protection of code and SQLite state remain necessary. Existing classified rows created before digest enforcement may have no digest; they fail closed and must be migrated under a new, reviewed handler identity rather than silently adopting the currently installed source. Back up the execution database and inventory gate policies before this schema upgrade.

## StateGraph / GraphAgentTeam compatibility

`portable.state_graph.StateGraph` and `portable.state_graph.GraphAgentTeam` are the supervisor-facing compatibility API over `portable.orchestration.Graph` and `Orchestrator`; `ExecutionController.execute_graph()` dispatches these types. Separately, `portable.agency_state_graph.StateGraph` is a lower-level checkpointable graph runtime with explicit state, edge, interrupt and bounded-superstep semantics. These are distinct classes in distinct modules and must not be treated as interchangeable without an explicit adapter and integration tests.

## Recovery

A failed graph execution is recorded as a supervisor failure. The existing default runtime permits bounded fresh retries through `auren_max_iterations` in the execution context, defaulting to three supervisor iterations. Each iteration has execution evidence; repeated failure should terminate rather than loop forever.

`portable.engineering_recovery.EngineeringRecoveryLedger` is wired into the default `AdaptiveRuntime.run()` path. It records each attempt and evidence, caps each same-hypothesis plan at two attempts, persists execution checkpoints, switches the exposed recovery mode to `alternate-plan`, and enforces a total attempt/time budget. `auren_max_iterations` is validated and capped at 12. The exact-head CI helper remains an explicit gate for engineering/release workflows; a general graph run cannot claim CI passed unless its caller supplies and verifies that evidence. The integration tests must remain green before [issue #377](https://github.com/sourabhJainR/AUREN/issues/377) can close.

## Service behavior

The existing service now resumes pending/running durable execution tasks before processing adaptive-learning maintenance. It remains under the platform's existing restart-on-failure service policy.

## Repository-level merge protection boundary

The runtime gate protects handlers registered with an operator-owned gate policy. It cannot intercept a merge performed directly through GitHub UI/API or an alternate automation identity. Repository owners/admins must configure and verify branch protection or rulesets that require the exact-head checks and appropriate review. Until those settings are independently confirmed, report this as an external blocker rather than claiming every merge path is protected.


## Mandatory-gate rollout and rollback

The registration guard intentionally rejects unknown and previously unclassified handlers. Roll out by inventorying each registered handler, reviewing its side effects, registering trusted context and exact-head CI evidence providers first, and then registering consequential handlers with both gate policies. Preserve a database backup and the last-known-good application artifact before rollout. If a deployment is blocked, do not relabel or rename the handler to bypass the guard; inspect the provider configuration and exact-head evidence.

**Rollback:** drain and stop workers, revoke privileged merge/deploy credentials from worker identities, preserve the evidence and execution databases, and restore the last-known-good application artifact only in a non-privileged/read-only mode until it supports the same gate contract. A code revert alone weakens this guard and does not undo external side effects; never re-enable privileged credentials on a reverted controller without independently enforced branch/environment protection. Re-run the negative bypass suite before restoring normal service. Repository branch protection remains a separate owner/admin setting.
