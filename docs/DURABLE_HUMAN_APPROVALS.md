# Durable human approvals

AUREN uses portable.durable_approval.DurableApprovalStore to persist human approval
for one exact operation. Construct it with the existing ExecutionController so the
approval tables share the controller's SQLite database and progress journal. Do not
create a second task store.

## Contract

- A request binds the stable task ID and operation ID to the action request fingerprint,
  artifact digest, evidence digest, policy digest, requester, role, scope and expiry.
- Repeating the same request returns the same approval ID. A changed binding supersedes
  a pending/approved request. An executing, unknown or completed operation cannot be
  silently replaced; use a new operation ID for a new logical action.
- Approval decisions require a trusted host identity/role/scope authorizer. The caller
  must not supply self-asserted roles as authority.
- Trusted approver and cancellation adapters must return literal boolean `True`; truthy strings, integers, missing adapters, and exceptions fail closed. High-risk requests can require
  multiple distinct approvers and separation of duties.
- Cancellation requires a separate trusted `cancellation_authorizer(actor, record)`.
  Missing, rejected, non-boolean, or failing authorization blocks cancellation. Only pending and
  approved requests can be cancelled. An executing or outcome-unknown operation cannot
  be cancelled to hide an uncertain side effect; reconcile the canonical side-effect
  ledger and provider evidence first. The actor value must come from authenticated host
  context, not a model-generated or user-supplied identity.
- Approval events are append-only and hash-chained. Progress is projected into the
  canonical ProgressJournal. reconcile_progress() repairs a missing latest projection.
- An approved operation is claimed atomically before dispatch. Only the winner receives
  an ApprovalReceipt; the existing ActionGovernanceGateway must use
  store.verify_receipt as its approval verifier. The gateway policy must require
  approval for the action.
- execute_approved_action() rechecks current artifact, evidence and policy digests
  before dispatch, then uses the existing gateway and DurableSideEffectLedger.
- A thrown exception or stale execution lease becomes outcome_unknown. It is not
  retryable. First reconcile the canonical side-effect ledger against provider operation
  history; then call reconcile_outcome() with a trusted verifier and immutable evidence.
- Waiting never occupies a worker. The host scheduler receives (task_id, approval_id)
  through wake_callback; approved_for_resume() and wake_approved() are bounded
  recovery fallbacks. The callback must resume the saved action stage from its checkpoint,
  not rerun retrieval/planning or trust model-generated task identity.

## Minimal integration

Create DurableApprovalStore with the live controller, a trusted identity adapter,
a separate cancellation_authorizer backed by authenticated operator context, a callback
that recomputes current artifact/evidence/policy digests, a scheduler wake callback, and
a trusted provider-reconciliation verifier.

Call create_request() with the ActionRequest, stable operation ID, content digests,
trusted requester ID, required approver role, exact scope and deadline. Persist the full
draft/checkpoint separately under its content digest. The current process can exit; no
worker remains blocked waiting for a person.

After an authorized decision, the host scheduler loads the saved action checkpoint and
calls execute_approved_action() with the same request and current digests. The
idempotency key must be stable for that logical operation. Remote exactly-once behavior
is not claimed unless the provider supports idempotency or authoritative operation lookup.

## Required tests

Run tests.test_durable_approval, tests.test_side_effect_ledger,
tests.test_side_effect_ledger_crash_fault_injection and tests.test_execution_boundary.

Coverage includes restart persistence, duplicate decisions, cancellation authorization and
cancel-before-dispatch safety, role/scope enforcement,
separation of duties, multi-approver thresholds, expiry, content invalidation, concurrent
claims, unknown remote outcomes, trusted reconciliation, progress recovery and audit-chain
tamper detection.

## Rollback

Disable the new approval adapter for new requests and keep existing approval records for
audit. If cancellation authorization is unavailable, leave cancellation fail-closed and
route the request through a trusted operator. Do not delete approval rows, replay an
unknown side effect, or bypass the existing action gateway and side-effect ledger. Rollback must preserve pending decisions and the
next safe action for operator recovery.
