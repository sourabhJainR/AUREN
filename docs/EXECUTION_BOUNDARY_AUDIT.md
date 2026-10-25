# Execution Boundary Audit: P0, P2, and P3

**Scope:** execution-surface inventory, task/revision/principal authority binding, and guarded recovery checkpoints. This work intentionally does not add a HarnessRouter integration or another provider/harness abstraction.

## P0 — Inventory the current execution surface

The code-owned inventory lives in `portable/execution_boundary.py` as `EXECUTION_SURFACE_INVENTORY` and `execution_surface_audit()`. It records the existing contracts and, importantly, the limits of each:

| Surface | Existing implementation | Control already present | Gap that remains |
|---|---|---|---|
| Task/repository/evidence identity | `portable/execution_contract.py:ExecutionEnvelope` | Validated task/plan/repository/evidence references and deterministic digest | Not automatically required by every action gateway caller |
| Action authorization | `portable/action_governance.py:ActionGovernanceGateway` | Capability/action allowlists, approval checks, external-effect idempotency, event hash chain | Callers must route consequential actions through the gateway; the trace is not trusted storage |
| MCP tool launch | `portable/vibe_coding_host.py:MCPStdioToolAdapter` | Consequential capabilities require a trusted resolver and `execute_bound_action` before `Popen`; run/task, provider operation, idempotency key, revision, digest and task budget are checked | Host must inject a resolver backed by trusted identity/repository context; no OS isolation or remote-outcome proof is implied |
| Runtime budget and cancellation | `portable/runtime_recovery.py:RuntimeExecutionGuard` | Bounded steps/attempts/deadline, cancellation, repeated-failure detection, digest-checked snapshot/resume | Durable behavior depends on persistence and cooperative worker cancellation |
| Engineering recovery | `portable/engineering_recovery.py:EngineeringRecoveryLedger` | SQLite-backed attempt/checkpoint ledger and exact-head CI helper | Does not sandbox workers or make external effects idempotent |
| Provider failover/resume | `portable/resumable_provider.py:ResumableProviderExecutor` | Pinned provider versions, task/stage identity, retry budget, persistence callbacks | Irreversible effects still require downstream idempotency and a durable outbox/inbox |

The inventory is deterministic and testable; it is not a dynamic proof that every runtime path uses these controls. The P0 registry also names every reviewed direct `ActionGovernanceGateway.execute()` call site and its disposition. An AST regression test scans production `portable/` code and fails when a direct call is added, removed, or moved without updating the reviewed registry. This is a change-detection gate—not proof that legacy callers are authority-bound. The next adoption step remains to migrate consequential callers to the bound path, with a call-site regression test for each integration.

## P2 — Bind authority to the actual execution

`ExecutionAuthorityBinding` binds one run/task to a repository digest and exact revision, principal, allowed capabilities/actions/targets, budget identifier, attempt limit, and optional expiry. Its digest changes when the scope changes. `authorize()` rejects mismatched identity, stale revision, unapproved capability/action, out-of-scope target, exhausted attempts, or expired authority. `execute_bound_action()` enforces that preflight, verifies the caller's budget identifier, charges the task-owned `RuntimeExecutionGuard` attempt before delegating to the existing `ActionGovernanceGateway`, and never invokes the handler on a binding denial. The supplied runtime guard cannot have a larger attempt ceiling than the authority binding.

This is an explicit preflight contract, not an OS sandbox or cryptographic identity service. The governed OpenShell adapter, AI coding agency bridge, and specialist team orchestrator expose authority-bound execution modes that use `execute_bound_action()`; direct gateway calls remain supported for compatibility and are not retroactively bound. The team orchestrator requires one authority and one task-owned guard per work unit when bound mode is enabled; incomplete maps fail closed. The coding bridge also requires explicit `allowed_read_paths` for authority-bound read-only tasks and `allowed_artifact_changes` for mutating tasks. These modes remain opt-in until each host integration can provide trusted repository identity, task authority, and a task-owned budget. Approval verification remains host-injected, and the host must obtain the repository revision from a trusted source rather than from the agent's own claim. File-system adapters should resolve symlinks and enforce canonical roots at the actual I/O boundary; lexical path checks alone are not sufficient for filesystem security.

## P3 — Preserve recovery state across restart

`GuardedExecutionCheckpoint` associates a `RuntimeExecutionGuard` snapshot with the authority digest, exact repository revision, sequence number, and prior-checkpoint digest. It verifies serialized checkpoint integrity, rejects unknown fields, authority/revision mismatch and a sequence older than the caller's persisted minimum, and restores the existing budget rather than creating a fresh one. Capture and restore both enforce that the nested attempt budget cannot exceed the authority ceiling.

Replay prevention requires the persistence owner to retain the highest accepted sequence in trusted storage and supply it as `minimum_sequence`. `ExecutionCheckpointStore` provides a SQLite-backed latest-checkpoint record with transaction-serialized monotonic sequence checks and predecessor-digest linkage. It keeps only the latest checkpoint per authority, not a full immutable history. A concurrent-writer regression test races two different checkpoints at the same sequence and verifies that SQLite transaction serialization accepts exactly one writer and preserves a single monotonic head. A digest is integrity evidence, not a signature; an attacker able to rewrite the database and recompute hashes is outside this store's threat boundary. Cancellation remains cooperative, and irreversible side effects require downstream idempotency.

## Regression gates

Run:

```bash
python -m unittest tests.test_execution_boundary tests.test_execution_contract -v
python -m unittest tests.test_runtime_recovery -v
```

The execution-boundary tests cover deterministic inventory, explicit direct-gateway call-site policy (including AST change detection), identity/scope/revision rejection, budget and expiry limits, authority digest changes, checkpoint tampering, exact-revision restore, replay-sequence rejection, preserved attempt charges, and concurrent checkpoint-writer serialization. If the runtime recovery test module is named differently in a future revision, discover the canonical test path rather than silently skipping it.

## Rollback

The changes are additive: the inventory and new contracts are introduced in `portable/execution_boundary.py`, with isolated regression tests. Rollback can revert the feature commit/PR without rewriting existing `ExecutionEnvelope`, `ActionRequest`, or `RuntimeExecutionGuard` formats. Do not remove existing guards as part of rollback. Before adopting the new binding at additional call sites, record baseline behavior and add call-site regression tests.


## Consequential MCP dispatch

Configured consequential MCP capabilities fail closed before process launch without a trusted `MCPExecutionAuthorization`. Product/research/web-search/accessibility/task-planning/independent-review/observability are read-only by default. Security tools default to authority-bound mode and may be marked `external_effects: false` only after operator verification. Custom tools must opt into the gate with `external_effects: true`. Read-only actions bind to a digest of the exact request; any non-read action requires a durable provider operation ID and idempotency key. This is application-level authority enforcement only. Provider-native idempotency, outcome lookup/reconciliation, and independently tested host isolation remain separate gates.
