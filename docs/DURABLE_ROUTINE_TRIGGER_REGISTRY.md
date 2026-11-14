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
does not implement an HTTP server or secret store.

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

    # Request cancellation of live occurrences; this does not claim completion.
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
routine before manual, scheduled or webhook dispatch is accepted. `concurrency_policy`
can be `allow` (the compatibility default) or `skip`. With `skip`, the registry checks
prior submitted occurrences through the canonical task lookup; pending, running, or
otherwise non-terminal work blocks a new occurrence and leaves a durable
`skipped_overlap` receipt. Missing task records or lookup failures produce a durable
`blocked_overlap` receipt and do not dispatch. This is a bounded skip policy, not a
queued/coalesced backlog; operators can retry with a new invocation/event ID after the
prior task reaches a terminal state.

The runtime adapters submit with a stable routine work key and reconcile uncertain
submissions through ExecutionController.task_by_work_key. A RoutineSpec may pin an
optional lowercase SHA-256 `profile_digest` and `policy_digest`; both values are copied
into the immutable occurrence receipt alongside the routine-definition digest. A
changed profile or policy therefore requires a new routine version and creates a new
evidence identity. These fields are evidence bindings only: the host must resolve and
validate the referenced profile and policy before registration or dispatch. A digest
in a receipt does not itself authorize execution or prove that a policy was enforced.
Approval-required routines remain blocked unless the host supplies an approval verifier. Webhook
authentication is a host-owned callback; the registry snapshots canonical JSON before
verification and dispatch so verifier-side mutation cannot change the accepted event.

## Safety and recovery contract

- Routine versions are immutable: a changed definition requires a higher version.
- Trigger occurrence identity is deterministic and unique in SQLite. Manual runs require
  a stable invocation ID; reusing an event or invocation ID with a different payload is rejected.
- Webhook event IDs are authenticated before a receipt is created.
- Approval-required routines fail closed when no trusted approval adapter approves.
- A durable dispatching receipt is written before calling the controller.
- A submission exception becomes unknown, not retryable failure. The registry
  calls an authoritative work-key lookup before it can mark the occurrence submitted.
  Without a reconciliation adapter, replay is refused.
- The canonical controller remains responsible for handler classification, role scope,
  deployment admission, budgets, deadlines, task leases, cancellation, and execution.
- `cancel_routine_occurrences` reconciles each occurrence by stable work key, then requests
  cancellation through `ExecutionController.request_cancellation`. The receipt records the
  request and reason digest; it remains non-terminal until the controller reports a terminal
  task state. Unknown task outcomes are not described as cancelled.
- Routine storage records configuration and trigger receipts only. It does not duplicate
  task state, queue ownership, or execution status.
- Interval ticks are bounded (maximum 256 occurrences per call); the host owns process
  supervision, clock discipline, scheduler availability, and cross-host leadership.

## Verification status and remaining deployment gates

tests/test_durable_routines.py covers version immutability, event authentication,
duplicate delivery, verifier mutation isolation, disabled-by-default behavior, approval
denial, unknown-outcome recovery, one-time scheduling, interval coalescing and the
fail-closed skip overlap policy. The
AdaptiveRuntime integration tests cover project isolation and canonical-controller
submission/reconciliation. These tests do not prove live provider webhook signatures,
concurrent schedule policy, profile pinning, cancellation propagation through every delegated tool, completion
summaries or multi-host failover.

Repository tests do not establish production scheduler failover, webhook signature
verification against a live provider, cross-host admission, or durable deployment
PostgreSQL behavior. Those remain deployment/operator acceptance work under #1193,
#909, and #404 as applicable. Do not report this module as a production scheduler.
