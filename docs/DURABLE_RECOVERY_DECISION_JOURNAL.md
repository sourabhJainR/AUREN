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

AdaptiveRuntime stores new decision receipts in the same SQLite database as the recovery ledger. Retry/alternate receipts and their attempt rows are committed in one transaction: a crash before commit leaves neither; a crash after commit leaves both. The previous separate journal is imported idempotently with receipt digests and original timestamps preserved, and its source file is retained for rollback/audit. Before resumed execution, reconciliation checks ordered attempt coverage, strategy/hypothesis agreement, blocked decisions, and checkpoint digests for legacy orphaned decisions. A mismatch fails closed before dispatch; legacy work with no receipts is reported as `legacy_unjournaled` rather than silently reinterpreting old history. External effects with ambiguous outcomes must still block until provider reconciliation confirms whether the effect occurred. Keep independent evaluator, isolation, exact-head CI, and rollback checks separate.

## Tests

```bash
python -m unittest tests.test_recovery_decision_journal -v
```

These tests cover restart reads, idempotent writes, conflicting writes, ordered history, invalid receipt fields, legacy journal migration, transaction rollback when receipt persistence fails, missing receipts for started attempts, and the AdaptiveRuntime no-replay boundary. Consequential external effects remain separately guarded by idempotency and trusted provider reconciliation.
