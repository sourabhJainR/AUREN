# AUREN Default Execution Controller

AdaptiveRuntime.run() is now the durable execution boundary for AUREN graph/team work.

## Default path

AdaptiveRuntime.run() -> ExecutionController -> AutonomousCompany -> existing Orchestrator(Graph).

This preserves the existing graph execution engine while making the supervisor the owner of task lifetime.

## Restart behavior

A session is mapped to a durable work unit. For a directly constructed `ExecutionController(root)`, its ledger is stored at `<root>/.auren/execution.sqlite3`. The default `AdaptiveRuntime` supplies `~/.auren/executive` as that root. On startup, `AurenStatePaths` copies known SQLite stores and session JSON from legacy `~/.aer` into matching canonical paths using atomic writes and SQLite backup. Legacy files are retained. If both roots contain conflicting data, startup fails closed with an actionable `StatePathConflict`; do not delete either copy before an explicit backup and reconciliation. See [issue #379](https://github.com/sourabhJainR/AUREN/issues/379) for migration rollout and live-environment verification.

If the worker exits after a work unit has started, the next invocation with the same session_id resumes the existing work unit rather than creating a new one.

The existing maintenance service calls resume_tasks() before its maintenance cycle. Its native service definitions already use restart-on-failure behavior, so host/process restart feeds back into the same durable queue.

## Durable handlers

Register an importable module-level handler with `runtime.register_task_handler(name, handler)`. Consequential promotion/merge handlers must register an operator-owned `promotion_gate_policy={"context_handler": "trusted-provider-name"}`; release workflows may also register `ci_gate_policy` with an exact head, required check names, and a separate trusted evidence provider. Submit with `runtime.submit_task(name, payload)` and resume with `runtime.resume_tasks()`. The context provider receives task identity only, not task payload, and must resolve repository/head/approver/canonical evidence from trusted live sources. Gate policy is persisted separately from task payload and re-registration cannot silently remove it.

Handlers are stored as module:qualname, not serialized Python objects. This makes them reconstructable by a new process.

## StateGraph / GraphAgentTeam compatibility

`portable.state_graph.StateGraph` and `portable.state_graph.GraphAgentTeam` are the supervisor-facing compatibility API over `portable.orchestration.Graph` and `Orchestrator`; `ExecutionController.execute_graph()` dispatches these types. Separately, `portable.agency_state_graph.StateGraph` is a lower-level checkpointable graph runtime with explicit state, edge, interrupt and bounded-superstep semantics. These are distinct classes in distinct modules and must not be treated as interchangeable without an explicit adapter and integration tests.

## Recovery

A failed graph execution is recorded as a supervisor failure. The existing default runtime permits bounded fresh retries through `auren_max_iterations` in the execution context, defaulting to three supervisor iterations. Each iteration has execution evidence; repeated failure should terminate rather than loop forever.

`portable.engineering_recovery.EngineeringRecoveryLedger` is wired into the default `AdaptiveRuntime.run()` path. It records each attempt and evidence, caps each same-hypothesis plan at two attempts, persists execution checkpoints, switches the exposed recovery mode to `alternate-plan`, and enforces a total attempt/time budget. `auren_max_iterations` is validated and capped at 12. The exact-head CI helper remains an explicit gate for engineering/release workflows; a general graph run cannot claim CI passed unless its caller supplies and verifies that evidence. The integration tests must remain green before [issue #377](https://github.com/sourabhJainR/AUREN/issues/377) can close.

## Service behavior

The existing service now resumes pending/running durable execution tasks before processing adaptive-learning maintenance. It remains under the platform's existing restart-on-failure service policy.

## Repository-level merge protection boundary

The runtime gate protects handlers registered with an operator-owned gate policy. It cannot intercept a merge performed directly through GitHub UI/API or an alternate automation identity. Repository owners/admins must configure and verify branch protection or rulesets that require the exact-head checks and appropriate review. Until those settings are independently confirmed, report this as an external blocker rather than claiming every merge path is protected.
