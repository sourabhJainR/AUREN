# Provider operation lifecycle and recovery boundary

Status: required implementation contract; runtime integration is not yet verified.

## Invariant

Every consequential provider operation has one stable operation ID and one immutable canonical intent. Persist both, along with the provider identity, intent digest, idempotency capability, and initial state, before the first network dispatch. The operation ID is derived from the stable logical operation key and intent digest, not an ephemeral worker attempt or regenerated task ID.

## Required lifecycle

1. **Prepare:** canonicalize and validate the target/action/business intent; reject secrets; derive the stable operation ID; durably persist intent, digest, provider/version, idempotency key, and state before dispatch. A key reused with changed intent or provider is a hard conflict.
2. **Dispatch:** atomically claim the prepared operation. Pass the stable idempotency key to the provider when supported. Persist dispatch-start evidence before crossing the network boundary. Never dispatch a record already in dispatching, pending, reconcile-required, blocked, or completed state.
3. **Confirm:** persist the provider operation ID, terminal status, receipt, and evidence references before marking the AUREN task complete. If receipt persistence fails after provider success, restart must reconcile rather than dispatch again.
4. **Reconcile:** after timeout, connection reset, worker/process death, or receipt-write failure, query provider operation status by stable operation ID/idempotency key where supported. Validate that returned evidence is bound to the intended provider, operation key, and intent.
5. **Retry:** retry only when provider-native idempotency makes replay safe, or trusted provider lookup authoritatively establishes no operation was accepted and the existing explicit retry authorization policy approves the retry. Never treat a timeout, missing response, empty lookup, or lookup error as proof of no effect.
6. **Block:** if the provider cannot establish the outcome, persist an explicit blocked/reconcile-required state and expose the operation ID, intent digest, provider, last known state, and required operator action. Do not automatically retry, switch providers, or mark success. Resolution requires authorized, evidence-linked operator action.

## State model

- prepared: intent and operation ID are durable; no dispatch has started.
- dispatching: the pre-dispatch claim is durable; remote acceptance may be uncertain after interruption.
- completed: a provider-confirmed immutable receipt is durable.
- pending: provider confirms the operation is still in progress; poll status with bounded scheduling, not redispatch.
- reconcile_required: outcome may be ambiguous and lookup has not resolved it.
- retry_ready: trusted reconciliation says retry is safe under the provider's idempotency contract; it is not itself a dispatch.
- blocked: outcome cannot be established or required trust evidence is missing; explicit resolution is required.
- failed: only a provider-confirmed terminal failure with evidence. A transport exception alone is not terminal failure.

Terminal receipts are immutable. State transitions must be compare-and-set or transactionally serialized. Every transition and reconciliation decision must append an audit/evidence event with timestamp, stable operation ID, intent digest, provider identity/version, prior/new state, and evidence references. Never persist credentials or secrets in intent, logs, or receipts.

## Provider adapter contract

Provider adapters declare capabilities explicitly:
- idempotency-key support and key scope/retention semantics;
- operation-status lookup support and whether “not found” is authoritative;
- provider operation ID extraction;
- mapping of provider statuses to pending, succeeded, or terminal failed;
- lookup consistency delay and bounded polling policy.

Capability claims must be backed by adapter-specific tests or provider documentation. Unknown capability defaults to unsupported. Provider failover is not a safe recovery strategy for an ambiguous write unless the original provider outcome is resolved first.

## Required failure-injection tests

- crash after intent persistence but before dispatch;
- crash after dispatch claim but before network call;
- provider accepts operation but response is lost;
- provider returns success but receipt persistence fails;
- duplicate worker dispatches the same operation concurrently;
- stable key reused with changed intent/provider;
- lookup says completed, pending, authoritative not-found, non-authoritative not-found, unknown, malformed, or errors;
- provider has neither idempotency nor status lookup;
- process restart restores blocked operations and does not dispatch them;
- explicit resolution is audited and cannot be authorized by a caller-supplied boolean alone.

Assert provider call counts, durable states, immutable receipts, operation-ID stability across restart, and absence of false success in every case.

## Integration sequence

1. Extend the existing durable side-effect ledger rather than creating a competing source of truth.
2. Persist the canonical intent and stable provider operation ID in the ledger before invoking consequential handlers.
3. Add provider adapter capability metadata and a common dispatch/reconcile contract.
4. Wire the lifecycle into ExecutionController, supervisor dispatch, progress journal, and evidence graph.
5. Add adapter-specific failure-injection tests, then run exact-head required CI and post-merge main verification.
6. Keep any provider without authoritative recovery blocked until an operator resolves it with evidence.

## Non-goals and safety

This contract does not claim distributed exactly-once execution. Local SQLite transactions cannot be atomic with an external provider. Provider cooperation, native idempotency/status lookup, durable receipts, and evidence-backed reconciliation are required. Never weaken the existing durable side-effect ledger, retry authorization, approval, security, or rollback gates to make an operation appear recoverable.
