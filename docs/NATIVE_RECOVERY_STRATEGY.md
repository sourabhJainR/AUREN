# Native Recovery Strategy Policy

AUREN uses reference systems such as Paperclip only to study general engineering techniques. AUREN does not import Paperclip runtime code, require Paperclip services, or delegate its authority, memory, task state, or security decisions to another system.

## This implementation slice

`portable/recovery_strategy.py` provides a deterministic policy decision for a supervisor that has already collected trusted runtime facts. It can select an untried strategy after a no-progress failure, permit a bounded retry when progress was recorded and no alternatives remain, or stop/block when safety conditions are not met.

The policy does not execute strategies, extend budgets, approve actions, validate a checkpoint itself, reconcile external providers, or declare a task successful. The caller must pass verified facts from AUREN's existing runtime guard, checkpoint/recovery ledger, and side-effect ledger. Until the controller is wired to this policy and tested end-to-end, this module is a policy primitive, not proof of automatic self-healing across the whole runtime.

## Decision order

1. Cancellation or expired deadline: stop.
2. Missing/unverified checkpoint: block.
3. Ambiguous or already-confirmed external effect: block for reconciliation; never replay it.
4. Exhausted recovery budget: block.
5. No progress: choose the first untried strategy from the caller's ordered allowlist.
6. Progress recorded and no untried strategy remains: allow one retry decision within the remaining budget.
7. No safe option: block with a reason.

Candidate strategies are stable IDs supplied by the trusted caller. Do not let model-generated text add capabilities, change authority, raise budgets, or bypass approval. Every selected strategy must run through the existing authorization, deadline, cancellation, evidence, and side-effect controls.

## Required integration gates

- Wire the policy into the durable execution controller at a single recovery decision point.
- Persist the chosen strategy, failure classification, attempted strategy IDs, budget remaining, checkpoint digest, and next safe action in the existing progress/evidence trail. Do not persist secrets or raw prompts.
- Propagate one cancellation event and absolute deadline through supervisor, worker, subprocess, and provider adapter.
- Verify hard termination, including descendants on supported operating systems; report termination failure as blocked/failed, never completed.
- Inject crashes before and after plan-store, recovery-ledger, progress-journal, and terminal-receipt commits. Reconcile stores before resuming.
- For external effects, require provider-native idempotency or history lookup and a durable outbox/inbox. An ambiguous result blocks until reconciled.
- Run the recovery acceptance matrix on exact PR head and after merge. Skipped checks are not passes.
- Keep independent evaluator/holdout custody, production isolation audit, branch protection, and operator rollback rehearsal as separate gates.

## Paperclip reference boundary

Potentially useful concepts to evaluate include durable task ownership, worker heartbeats, watchdog detection, approval queues, audit history, bounded retries, and recovery after worker loss. Reimplement only the needed contracts behind AUREN-owned interfaces. No Paperclip dependency or runtime coupling is introduced by this slice.

## Tests

Run:

```bash
python -m unittest tests.test_recovery_strategy -v
```

These unit tests validate the pure decision policy only. They do not prove controller integration, subprocess termination, external side-effect recovery, sandbox isolation, or independent evaluation.
