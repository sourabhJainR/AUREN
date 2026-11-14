# Durable routine trigger registry

Tracking: issue #1216.

The registry provides project-scoped durable routine definitions and idempotent occurrence receipts.
It is deliberately not a scheduler, task queue, approval store, or execution runtime.
AdaptiveRuntime binds it to the canonical ExecutionController; the existing
MaintenanceService tick dispatches due one-time and interval occurrences. No second
daemon or scheduler is created.

## Supported triggers

- Manual: explicitly dispatch a registered routine.
- One-time: dispatch at an ISO-8601 timestamp.
- Interval: dispatch at a bounded interval after registration; a tick is supplied by
  an already managed service or operator scheduler.
- Webhook: accept an event only after a caller-supplied authentication verifier
  validates the event identity and payload.

Webhook verification must validate the provider signature, timestamp/replay window,
and endpoint identity using secrets held outside the routine payload. The registry
does not implement an HTTP server or secret store. For providers using HMAC-SHA256,
`verify_hmac_webhook_signature` supplies a reusable timestamped verifier. It signs
the newline-separated timestamp, routine ID, event ID and canonical JSON payload;
the host extracts provider headers and retrieves the secret from its secret store:

    from portable.durable_routines import verify_hmac_webhook_signature

    def host_signature_verifier(routine_id, event_id, payload):
        return verify_hmac_webhook_signature(
            webhook_secret,
            request_timestamp,
            request_signature,
            routine_id=routine_id,
            event_id=event_id,
            payload=payload,
            tolerance_seconds=300,
        )

The routine registry then deduplicates the authenticated event ID. Providers with
different signature formats or canonicalization rules still need a provider-specific
host adapter; do not assume this generic format matches every provider.

## Minimal integration

The host already owns an AdaptiveRuntime and a project root. Register and enable a
routine through that runtime; do not create a second scheduler or controller.

    from pathlib import Path
    from portable.durable_routines import RoutineSpec

    project_root = Path(".").resolve()
    spec = RoutineSpec(
        routine_id="repository-health",
        version=1,
        handler="repository_health_check",
        payload={"repository": "owner/repo"},
        trigger="interval",
        interval_seconds=3600,
        timezone="UTC",
        deadline_seconds=900,
    )
    runtime.register_routine(project_root, spec)
    runtime.set_routine_enabled(project_root, "repository-health", True)

    # The managed MaintenanceService calls this on its existing bounded tick.
    runtime.dispatch_due_routines(project_root, limit=20)

    # Manual runs use a caller-generated stable invocation ID.
    runtime.dispatch_manual_routine(
        project_root, "repository-health", invocation_id="operator-request-001",
    )

    # Request cancellation of every live occurrence for this routine.
    runtime.cancel_routine_occurrences(
        project_root, "repository-health", reason="operator requested stop",
    )

    # The host must verify the provider signature, timestamp and replay window.
    runtime.dispatch_webhook_routine(
        project_root, "repository-triage", event_id="provider-event-123",
        event_type="push", payload={"ref": "refs/heads/main"},
        authenticate=host_signature_verifier,
    )

## Runtime integration

Routine databases are stored below AUREN's canonical state root under a project-key
namespace. This keeps routines and event IDs isolated between repositories. The
maintenance service calls the bounded due-routine dispatcher as part of its existing
tick; the routine registry does not own process lifetime or scheduling wakeups. A
routine-store failure is reported separately and does not suppress the existing
adaptive-maintenance lane.
New RoutineSpec values default to disabled. An operator must explicitly enable a
routine before manual, scheduled or webhook dispatch is accepted. The optional
`concurrency_policy` is `allow` by default or `skip`. Under `skip`, admission is
serialized in the registry transaction and checks submitted, dispatching, unknown,
and cancellation-requested occurrences across routine versions. Every prior task
must be authoritatively reconciled to a terminal state before another occurrence is
submitted. Missing task records, lookup errors, and more than 64 unresolved candidates
fail closed. A concurrently reserved dispatching occurrence blocks a second run;
it is not treated as absent.

The runtime adapters submit with a stable routine work key and reconcile uncertain
submissions through ExecutionController.task_by_work_key. A RoutineSpec may pin an
optional lowercase SHA-256 `profile_digest` and `policy_digest`; both values are copied
into the immutable occurrence receipt alongside the routine-definition digest. A
changed profile or policy therefore requires a new routine version and creates a new
evidence identity. These fields are evidence bindings only: the host must resolve and
validate the referenced profile and policy before registration or dispatch. A digest
in a receipt does not itself authorize execution or prove that a policy was enforced.
Approval-required routines persist a `blocked_approval` receipt when approval is missing, denied, expired or invalid. A later approved retry may resume that same occurrence while the routine remains enabled and on the same version. Structured approval callbacks should return `approved`, `approval_id`, `approver`, `scope`, and a lowercase SHA-256 `decision_digest`; optional `expires_at` is checked before dispatch. Boolean callbacks remain supported for compatibility but produce a weaker adapter-result receipt. Webhook
authentication is a host-owned callback; the registry snapshots canonical JSON before
verification and dispatch so verifier-side mutation cannot change the accepted event.

## Safety and recovery contract

- Routine versions are immutable: a changed definition requires a higher version.
- Trigger occurrence identity is deterministic and unique in SQLite. Manual runs require
  a stable invocation ID; reusing an event or invocation ID with a different payload is rejected.
- Webhook event IDs are authenticated before a receipt is created.
- Approval-required routines fail closed and persist the decision receipt; structured receipts bind approval ID, approver, scope, decision digest and optional expiry.
- A durable dispatching receipt is written before calling the controller.
- A submission exception becomes unknown, not retryable failure. The registry
  calls an authoritative work-key lookup before it can mark the occurrence submitted.
  Without a reconciliation adapter, replay is refused.
- The canonical controller remains responsible for handler classification, role scope,
  deployment admission, budgets, deadlines, task leases, cancellation, and execution.
- `cancel_routine_occurrence` records a cancellation request and calls the durable
  controller cancellation API. The occurrence remains `cancellation_requested`, not
  `cancelled`, until the controller reports a terminal outcome.
- `cancel_routine_occurrences` reconciles every non-terminal occurrence by stable work key,
  then requests cancellation through the canonical controller. Missing or uncertain task
  records remain unknown; terminal states are copied from the controller into the receipt.
- Routine storage records configuration and trigger receipts only. It does not duplicate
  task state, queue ownership, or execution status.
- Interval ticks are bounded (maximum 256 occurrences per call); the host owns process
  supervision, clock discipline, scheduler availability, and cross-host leadership.

## Verification status and remaining deployment gates

tests/test_durable_routines.py covers version immutability, event authentication,
duplicate delivery, verifier mutation isolation, disabled-by-default behavior, approval
denial, unknown-outcome recovery, one-time scheduling, interval coalescing,
fail-closed overlap admission (including concurrent dispatching occurrences and
prior routine revisions), durable approval decisions, cancellation request receipts
and legacy definition upgrades, plus bulk cancellation reconciliation.
The AdaptiveRuntime integration tests cover project isolation, canonical-controller
submission/reconciliation and durable cancellation wiring. These tests do not prove
live provider webhook signatures, completion summaries or multi-host failover.

Repository tests do not establish production scheduler failover, webhook signature
verification against a live provider, cross-host admission, or durable deployment
PostgreSQL behavior. Those remain deployment/operator acceptance work under #1193,
#909, and #404 as applicable. Do not report this module as a production scheduler.
