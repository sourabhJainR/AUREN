# Durable downstream side-effect idempotency

AUREN validates canonical plan checkpoints before dispatch and again before a queued
engineering-plan handler runs. That prevents stale or forged work from crossing the
dispatch boundary; it does not make an external API call atomic with AUREN's SQLite
transaction.

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
- The ledger is an explicit handler integration contract; ordinary handlers are not
  silently assumed to be idempotent. Production rollout still needs failure-injection
  evidence against each consequential adapter.

## Validation

`tests/test_side_effect_ledger.py` covers replay after restart, payload-binding
conflicts, interrupted execution, operator-gated retry, immutable receipts, and
invalid state transitions. Repository CI is implementation evidence only; it is not
proof of a third-party provider's idempotency or production isolation.
