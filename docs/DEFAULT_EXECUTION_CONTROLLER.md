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

Register an importable module-level handler with runtime.register_task_handler(name, handler, project_root=project_root).
Submit with runtime.submit_task(name, payload, project_root=project_root).
Resume with runtime.resume_tasks(project_root=project_root).

Handlers are stored as module:qualname, not serialized Python objects. This makes them reconstructable by a new process.

## StateGraph / GraphAgentTeam compatibility

`portable.state_graph.StateGraph` and `portable.state_graph.GraphAgentTeam` are the supervisor-facing compatibility API over `portable.orchestration.Graph` and `Orchestrator`; `ExecutionController.execute_graph()` dispatches these types. Separately, `portable.agency_state_graph.StateGraph` is a lower-level checkpointable graph runtime with explicit state, edge, interrupt and bounded-superstep semantics. These are distinct classes in distinct modules and must not be treated as interchangeable without an explicit adapter and integration tests.

## Recovery

A failed graph execution is recorded as a supervisor failure. The existing default runtime permits bounded fresh retries through `auren_max_iterations` in the execution context, defaulting to three supervisor iterations. Each iteration has execution evidence; repeated failure should terminate rather than loop forever.

`portable.engineering_recovery.EngineeringRecoveryLedger` adds durable failure signatures, hypothesis budgets, checkpoints and an exact-head CI gate, but it is not yet directly integrated into this default execution path. Issue [#377](https://github.com/sourabhJainR/AUREN/issues/377) remains open until the real supervisor path enforces those guarantees and integration tests prove them.

## Service behavior

The existing service now resumes pending/running durable execution tasks before processing adaptive-learning maintenance. It remains under the platform's existing restart-on-failure service policy.