# Execution Boundary Adoption: Durable Checkpoints and Sandbox Enforcement

This follow-on increment makes the execution-boundary contracts operational in two specific places without changing the legacy API by default.

## Durable recovery state

`portable.execution_boundary.ExecutionCheckpointStore` persists the latest guarded checkpoint in SQLite under the authority digest. It serializes writers with `BEGIN IMMEDIATE`, checks repository revision, enforces strictly increasing sequence numbers, and requires the new checkpoint to reference the exact prior checkpoint digest. Reads validate the row against the serialized checkpoint and the authority binding before restoring the existing runtime budget.

Use a host-owned database path with restrictive filesystem permissions. SQLite protects concurrent application writes; it does not protect against a privileged attacker who can rewrite the database and recompute all hashes. The store keeps the latest checkpoint per authority, not a complete immutable history. If a complete audit history is required, append each signed checkpoint receipt to the existing trusted evidence store as a separate operation.

## Governed sandbox adoption

`portable.governed_sandbox_execution.execute_governed_sandbox()` now accepts an optional `execution_authority` plus the trusted repository revision/digest, budget identifier, and task-owned `RuntimeExecutionGuard`. When supplied, the adapter uses `execute_bound_action()` before invoking the sandbox. Missing bound-execution arguments fail closed, and a mismatched revision, repository digest, principal, task, capability, action, target, budget, expiry, or attempt ceiling prevents sandbox launch.

The compatibility path remains available when callers do not pass an authority. This increment does **not** silently claim every direct `ActionGovernanceGateway` caller has migrated. Next adoption targets are `AgentTeamOrchestrator` and `run_coding_task()`; they need a trusted repository identity and budget lifecycle from their host rather than a fabricated revision.

## Regression coverage

- Monotonic sequence and predecessor digest are checked under the SQLite transaction.
- A forked checkpoint chain and an authority mismatch are rejected.
- Restore resumes prior attempt charges.
- Governed sandbox launch is blocked on stale repository revision.
- A correctly bound sandbox launch consumes the existing runtime budget.

Run the execution-boundary and governed-sandbox tests, then the repository's full exact-head CI suite before merge.
