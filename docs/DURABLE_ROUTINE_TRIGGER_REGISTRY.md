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

    from portable.durable_routines import DurableRoutineRegistry, RoutineSpec

    registry = DurableRoutineRegistry(".auren/routines.sqlite")
    registry.register(RoutineSpec(
        routine_id="repository-health",
        version=1,
        handler="repository_health_check",
        payload={"repository": "owner/repo"},
        trigger="interval",
        interval_seconds=3600,
        timezone="UTC",
        deadline_seconds=900,
        enabled=True,  # New routines are disabled unless the operator opts in.
    ))

    # A manual run uses a caller-generated stable invocation ID for idempotency.
    registry.dispatch_manual(
        "repository-health", invocation_id="operator-request-2026-11-07-001",
        submit=lambda handler, payload, **kwargs: controller.submit(handler, payload, **kwargs),
    )

    # The adapter is the canonical ExecutionController instance.
    def submit(handler, payload, **kwargs):
        return controller.submit(handler, payload, **kwargs)

    # Preferred host path: the project-scoped registry and controller adapter
    # are composed by AdaptiveRuntime. The managed MaintenanceService invokes
    # dispatch_due_routines() on its existing bounded tick.
    runtime.register_routine(project_root, spec)
    runtime.set_routine_enabled(project_root, "repository-health", True)
    runtime.dispatch_due_routines(project_root, limit=20)

    # Manual and webhook occurrences use the same canonical controller.
    runtime.dispatch_manual_routine(
        project_root, "repository-health", invocation_id="operator-request-001",
    )
    runtime.dispatch_webhook_routine(
        project_root, "repository-triage", event_id="provider-event-123",
        event_type="push", payload={"ref": "refs/heads/main"},
        authenticate=host_signature_verifier,
    )

## Runtime integration

Routine databases are stored below AUREN's canonical state root under a project-key
namespace. This keeps routines and event IDs isolated between repositories. The
maintenance service calls the bounded due-routine dispatcher as part of its existing
tick; the routine registry does not own process lifetime or scheduling wakeups.
New RoutineSpec values default to disabled. An operator must explicitly enable a
routine before manual, scheduled or webhook dispatch is accepted.

The runtime adapters submit with a stable routine work key and reconcile uncertain
submissions through ExecutionController.task_by_work_key. Approval-required
routines remain blocked unless the host supplies an approval verifier. Webhook
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
- Routine storage records configuration and trigger receipts only. It does not duplicate
  task state, queue ownership, or execution status.
- Interval ticks are bounded (maximum 256 occurrences per call); the host owns process
  supervision, clock discipline, scheduler availability, and cross-host leadership.

## Verification status and remaining deployment gates

tests/test_durable_routines.py covers version immutability, event authentication,
duplicate delivery, verifier mutation isolation, disabled-by-default behavior, approval
denial, unknown-outcome recovery, one-time scheduling and interval coalescing. The
AdaptiveRuntime integration tests cover project isolation and canonical-controller
submission/reconciliation. These tests do not prove live provider webhook signatures,
concurrent schedule policy, profile pinning, completion summaries or multi-host failover.

Repository tests do not establish production scheduler failover, webhook signature
verification against a live provider, cross-host admission, or durable deployment
PostgreSQL behavior. Those remain deployment/operator acceptance work under #1193,
#909, and #404 as applicable. Do not report this module as a production scheduler.
