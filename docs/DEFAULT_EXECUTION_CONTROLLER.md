# AUREN Default Execution Controller

AdaptiveRuntime.run() is now the durable execution boundary for AUREN graph/team work.

## Default path

AdaptiveRuntime.run() -> ExecutionController -> AutonomousCompany -> existing Orchestrator(Graph).

This preserves the existing graph execution engine while making the supervisor the owner of task lifetime.

## Restart behavior

A session is mapped to a durable work unit. The mapping and execution ledger live under the project .auren directory.

If the worker exits after a work unit has started, the next invocation with the same session_id resumes the existing work unit rather than creating a new one.

The existing maintenance service calls resume_tasks() before its maintenance cycle. Its native service definitions already use restart-on-failure behavior, so host/process restart feeds back into the same durable queue.

## Durable handlers

Register an importable module-level handler with runtime.register_task_handler(name, handler, project_root=project_root).
Submit with runtime.submit_task(name, payload, project_root=project_root).
Resume with runtime.resume_tasks(project_root=project_root).

Handlers are stored as module:qualname, not serialized Python objects. This makes them reconstructable by a new process.

## StateGraph / GraphAgentTeam compatibility

The current AUREN tree's concrete execution primitive is Graph + Orchestrator; there are no classes named StateGraph or GraphAgentTeam in the current source tree. ExecutionController.execute_graph() is the compatibility boundary for callers using those graph/team concepts, while the real AdaptiveRuntime path remains unchanged at the graph engine level.

No second graph implementation was introduced.

## Recovery

A failed graph execution is recorded as a supervisor failure. The default runtime permits bounded fresh retries through auren_max_iterations in the execution context, defaulting to three supervisor iterations.

Each iteration has its own execution evidence. A successful iteration terminates the work unit; repeated failure produces a terminal failure and trust evidence rather than silently looping forever.

## Service behavior

The existing service now resumes pending/running durable execution tasks before processing adaptive-learning maintenance. It remains under the platform's existing restart-on-failure service policy.