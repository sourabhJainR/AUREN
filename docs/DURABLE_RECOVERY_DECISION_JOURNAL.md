# Durable Recovery Decision Journal

The module `portable/recovery_decision_journal.py` provides an append-only, SQLite-backed receipt store for decisions made by AUREN's bounded recovery policy.

## What it guarantees

- A stable `(work_id, phase, attempt_number)` key allows a worker to safely repeat the same journal write after a restart.
- Replaying the same decision returns the original receipt; attempting to replace that key with a different decision fails closed.
- Each receipt has a SHA-256 digest over canonical decision fields and stores the checkpoint digest used for the decision.
- Queries return one decision or a work item's decisions in attempt order.
- Inputs are bounded identifier tokens. Do not put prompts, credentials, raw provider output, or secrets in receipt metadata.

## Safety boundary

This journal records a decision; it does not execute a strategy, verify the checkpoint, approve a consequential action, or reconcile external effects. The caller must derive checkpoint digest, failure classification, side-effect state, and attempt budget from trusted AUREN-owned state. A decision receipt is not evidence that the chosen strategy succeeded.

AdaptiveRuntime now reconciles journal receipts against the durable recovery ledger before a resumed run. It checks ordered attempt coverage, strategy/hypothesis agreement, blocked decisions, and checkpoint digests for a decision persisted before its attempt row. A mismatch or orphaned decision fails closed before dispatch; legacy work with no receipts is reported as `legacy_unjournaled` so rollout does not silently reinterpret old history. This is detection and fail-closed recovery, not cross-database atomicity: an interrupted pending decision currently requires an explicit recovery path rather than automatic replay. External effects with ambiguous outcomes must still block until provider reconciliation confirms whether the effect occurred. Keep independent evaluator, isolation, exact-head CI, and rollback checks separate.

## Tests

```bash
python -m unittest tests.test_recovery_decision_journal -v
```

These tests cover restart reads, idempotent writes, conflicting writes, ordered history, invalid receipt fields, decision-before-attempt interruption, missing receipts for started attempts, and the AdaptiveRuntime no-replay boundary. They do not prove cross-store atomicity or automatic resumption from an interrupted pending decision.
