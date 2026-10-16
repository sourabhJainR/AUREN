# Execution Boundary Audit: P0, P2, and P3

**Scope:** execution-surface inventory, task/revision/principal authority binding, and guarded recovery checkpoints. This work intentionally does not add a HarnessRouter integration or another provider/harness abstraction.

## P0 — Inventory the current execution surface

The code-owned inventory lives in `portable/execution_boundary.py` as `EXECUTION_SURFACE_INVENTORY` and `execution_surface_audit()`. It records the existing contracts and, importantly, the limits of each:

| Surface | Existing implementation | Control already present | Gap that remains |
|---|---|---|---|
| Task/repository/evidence identity | `portable/execution_contract.py:ExecutionEnvelope` | Validated task/plan/repository/evidence references and deterministic digest | Not automatically required by every action gateway caller |
| Action authorization | `portable/action_governance.py:ActionGovernanceGateway` | Capability/action allowlists, approval checks, external-effect idempotency, event hash chain | Callers must route consequential actions through the gateway; the trace is not trusted storage |
| Runtime budget and cancellation | `portable/runtime_recovery.py:RuntimeExecutionGuard` | Bounded steps/attempts/deadline, cancellation, repeated-failure detection, digest-checked snapshot/resume | Durable behavior depends on persistence and cooperative worker cancellation |
| Engineering recovery | `portable/engineering_recovery.py:EngineeringRecoveryLedger` | SQLite-backed attempt/checkpoint ledger and exact-head CI helper | Does not sandbox workers or make external effects idempotent |
| Provider failover/resume | `portable/resumable_provider.py:ResumableProviderExecutor` | Pinned provider versions, task/stage identity, retry budget, persistence callbacks | Irreversible effects still require downstream idempotency and a durable outbox/inbox |

The inventory is deterministic and testable; it is not a dynamic proof that every runtime path uses these controls. Follow-up adoption should trace actual call sites and ensure all consequential actions go through the same canonical governance path.

## P2 — Bind authority to the actual execution

`ExecutionAuthorityBinding` binds one run/task to a repository digest and exact revision, principal, allowed capabilities/actions/targets, budget identifier, attempt limit, and optional expiry. Its digest changes when the scope changes. `authorize()` rejects mismatched identity, stale revision, unapproved capability/action, out-of-scope target, exhausted attempts, or expired authority. `execute_bound_action()` enforces that preflight, verifies the caller's budget identifier, charges the task-owned `RuntimeExecutionGuard` attempt before delegating to the existing `ActionGovernanceGateway`, and never invokes the handler on a binding denial. The supplied runtime guard cannot have a larger attempt ceiling than the authority binding.

This is an explicit preflight contract, not an OS sandbox or cryptographic identity service. Callers with a binding should use `execute_bound_action()` rather than bypassing the wrapper; direct gateway calls remain supported for compatibility and are not retroactively bound. Approval verification remains host-injected, and the host must obtain the repository revision from a trusted source rather than from the agent's own claim. File-system adapters should resolve symlinks and enforce canonical roots at the actual I/O boundary; lexical path checks alone are not sufficient for filesystem security.

## P3 — Preserve recovery state across restart

`GuardedExecutionCheckpoint` associates a `RuntimeExecutionGuard` snapshot with the authority digest, exact repository revision, sequence number, and prior-checkpoint digest. It verifies serialized checkpoint integrity, rejects authority/revision mismatch and a sequence older than the caller's persisted minimum, and restores the existing budget rather than creating a fresh one.

Replay prevention requires the persistence owner to retain the highest accepted sequence in trusted storage and supply it as `minimum_sequence`. A digest is integrity evidence, not a signature; an attacker able to rewrite both checkpoint and digest is outside this value object's threat boundary. Cancellation remains cooperative, and irreversible side effects require downstream idempotency.

## Regression gates

Run:

```bash
python -m unittest tests.test_execution_boundary tests.test_execution_contract -v
python -m unittest tests.test_runtime_recovery -v
```

The execution-boundary tests cover deterministic inventory, identity/scope/revision rejection, budget and expiry limits, authority digest changes, checkpoint tampering, exact-revision restore, replay-sequence rejection, and preserved attempt charges. If the runtime recovery test module is named differently in a future revision, discover the canonical test path rather than silently skipping it.

## Rollback

The changes are additive: the inventory and new contracts are introduced in `portable/execution_boundary.py`, with isolated regression tests. Rollback can revert the feature commit/PR without rewriting existing `ExecutionEnvelope`, `ActionRequest`, or `RuntimeExecutionGuard` formats. Do not remove existing guards as part of rollback. Before adopting the new binding at additional call sites, record baseline behavior and add call-site regression tests.
