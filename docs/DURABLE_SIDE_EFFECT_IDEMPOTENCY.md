# Durable downstream side-effect idempotency

AUREN validates canonical plan checkpoints before dispatch and again before a queued
engineering-plan handler runs. That prevents stale or forged work from crossing the
dispatch boundary; it does not make an external API call atomic with AUREN's SQLite
transaction.

For generic consequential tasks that are not canonical engineering-plan dispatches, a caller may pass `auren_idempotency: {"key": "<stable-logical-operation-key>"}` in the task payload. This metadata is removed from the payload digest and binds ledger replay to the stable key rather than the transient task ID. Reuse the key only for the same logical operation and unchanged business payload; the ledger rejects changed payloads. This is an AUREN-side deduplication/reconciliation contract, not a replacement for provider-native idempotency or provider operation-history lookup.

For consequential handlers (deployment, payment, publishing, destructive mutation,
or any external write), use `portable.side_effect_ledger.DurableSideEffectLedger`
with a stable key derived from the canonical run/task/action identity.

## Handler protocol

1. Build a canonical payload containing the intended operation and target. Never put
   secrets or credentials in the payload.
2. Call `begin(key, payload)` before the external operation.
3. Execute the operation only when the decision is `execute`. A `replay` decision
   means return the recorded receipt without repeating the operation. A `reconcile`
   decision means stop and resolve the remote outcome; do not call the provider.
4. After the provider confirms the operation, persist a stable provider operation ID,
   result status, and relevant digest using `complete(key, receipt)`.
5. If the call outcome is ambiguous (timeout, connection reset, worker death), require
   reconciliation. A retry requires a host-injected trusted authorizer to validate the
   operator identity and provider/evidence reference, plus explicit no-effect confirmation.
   The caller-supplied boolean alone is never treated as authorization.

## Guarantees and limits

- Key reuse with a different canonical payload is rejected.
- Completed receipts are immutable and replayable after process restart.
- An interrupted in-flight record cannot be automatically executed a second time.
- Concurrent callers serialize the state transition in SQLite.
- A retry requires explicit no-effect confirmation, operator identity, evidence reference,
  and a trusted host-injected authorization callback; the default without a verifier is deny.
- Every accepted retry authorization is retained in a separate append-only SQLite audit table
  with an authorization digest, so later execution/completion cannot erase the approval trail.
- This is not distributed exactly-once execution. The strongest design combines this
  ledger with provider-native idempotency keys, a transactional outbox/inbox, and
  provider-side operation lookup. Without provider cooperation, an ambiguous outcome
  must remain blocked for reconciliation.
- `ExecutionController.run_once` now wraps every handler registered as `consequential`
  with this ledger before provider invocation. Completed mapping receipts are replayed
  without invoking the handler again; `reconcile` decisions fail closed before dispatch.
  Engineering-plan dispatches bind the ledger to the stable dispatch key rather than
  a transient task ID, so recreating a task for the same canonical dispatch reuses its
  receipt or blocks for reconciliation. Ordinary tasks use their durable task ID. Distinct
  dispatches still require a stable provider idempotency key to deduplicate at the provider.
- Production rollout still needs failure-injection evidence against each consequential adapter.

## Validation

`tests/test_side_effect_ledger.py` covers replay after restart, payload-binding
conflicts, interrupted execution, operator-gated retry, immutable receipts, and
invalid state transitions, stable dispatch replay across recreated task IDs, and unknown provider outcomes that must not redispatch. Repository CI is implementation evidence only; it is not
proof of a third-party provider's idempotency or production isolation.

## Receipt-persistence fault injection

The regression suite injects a durable receipt-write failure after the provider returns
success. The original ledger row remains `executing`; after process restart, a repeated
`begin(key, same_payload)` must transition it to `reconcile_required` and return
`reconcile`, never `execute`. The provider call count must remain one. The operator or
trusted reconciler must inspect the provider's operation history before any retry; a
retry is permitted only through the existing trusted authorization path with evidence
that no effect occurred.

## Process-crash fault injection

`tests/test_side_effect_ledger_crash_fault_injection.py` uses spawned child processes that terminate with `os._exit` at two boundaries:

- **After durable claim, before external effect:** restart converts the lingering `executing` row to `reconcile_required`. A retry is blocked until a trusted authorizer validates operator identity, evidence reference, reason, and confirmation that no effect occurred.
- **After provider acceptance, before receipt persistence:** restart returns `reconcile`, not `execute`. A trusted reconciliation verifier binds provider-operation evidence to the idempotency key and receipt; replay returns the immutable receipt without a second provider dispatch.

Run: `python -m unittest tests.test_side_effect_ledger_crash_fault_injection -v`.

These tests demonstrate the local ledger's restart behavior. They do not prove every external provider's idempotency, that remote effects are atomic with SQLite, or that production credentials/host access are isolated.

## Recovery after the provider receipt but before the supervisor receipt

The inner provider-operation ledger and the outer supervisor idempotency row are separate durable records. A process or storage failure can leave the inner receipt completed while the outer row remains `executing`. On resume, AUREN first validates the inner receipt against the stable operation identity and intent digest. If it is complete and immutable, AUREN reconstructs the outer receipt from that record without contacting or redispatching to the provider. The local evidence reference is accepted only when a fresh ledger read matches the exact nested receipt and operation ID. Missing, corrupt, mismatched, or non-terminal child records remain blocked. If the child record is only `reconcile_required`, AUREN still requires provider history lookup and the configured trusted verifier; unknown provider outcomes never authorize a retry.

The regression test injects a failure only at the outer receipt commit, restarts the controller, and confirms that the durable inner receipt closes the outer record with a single provider invocation and no provider adapter available. This proves local cross-ledger recovery for that boundary; it does not prove provider-native idempotency or distributed exactly-once execution.


## Rollback

If the recovery path regresses, revert this commit/PR. The prior behavior leaves the outer operation blocked and does not redispatch; do not bypass either ledger, weaken receipt verification, or authorize a retry to restore availability. Keep all corruption and crash-injection tests enabled.
