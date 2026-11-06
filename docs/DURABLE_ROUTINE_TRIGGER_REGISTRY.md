# Durable routine trigger registry

Tracking: issue #1216.

The registry provides durable routine definitions and idempotent occurrence receipts.
It is deliberately not a scheduler, task queue, approval store, or execution runtime.
Every occurrence is submitted through the canonical ExecutionController.submit API.

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
    ))

    # A manual run uses a caller-generated stable invocation ID for idempotency.
    registry.dispatch_manual(
        "repository-health", invocation_id="operator-request-2026-11-07-001",
        submit=lambda handler, payload, **kwargs: controller.submit(handler, payload, **kwargs),
    )

    # The adapter is the canonical ExecutionController instance.
    def submit(handler, payload, **kwargs):
        return controller.submit(handler, payload, **kwargs)

    # Called by an existing managed service tick; this module creates no daemon.
    registry.dispatch_due(
        now=clock.now(),
        submit=submit,
        approval=trusted_approval_adapter,
        reconcile=controller_lookup_by_work_key,
    )

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
duplicate delivery, approval denial, unknown-outcome recovery, one-time scheduling,
interval scheduling, and disabled routines.

Repository tests do not establish production scheduler failover, webhook signature
verification against a live provider, cross-host admission, or durable deployment
PostgreSQL behavior. Those remain deployment/operator acceptance work under #1193,
#909, and #404 as applicable. Do not report this module as a production scheduler.
