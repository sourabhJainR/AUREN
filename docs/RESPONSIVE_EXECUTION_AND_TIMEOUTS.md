# Responsive Execution Contract

**Applies to:** AUREN supervisor, graph nodes, provider adapters, subprocesses, model routing, repository operations, CI orchestration, dashboard sessions, and recovery handlers.

## Goal

No single step may hold the whole engineering workflow indefinitely. Responsiveness means bounded waiting, visible progress, cancellation, recoverability, and truthful terminal status—not merely returning quickly or hiding errors.

## Mandatory execution contract

1. **Deadline hierarchy.** Every run has an absolute deadline; each phase, provider/model call, subprocess, network request, lock acquisition, and validation command has a shorter local timeout. A child deadline may never extend its parent deadline. Validate timeouts as finite positive values.
2. **Progress heartbeat.** Persist the current run/session ID, phase, task/attempt ID, last-progress timestamp, deadline, heartbeat/lease, and current wait reason. Report progress only when a state transition or measurable work occurs; repeated heartbeat updates must not masquerade as progress.
3. **Cancellation and interruption.** Poll a shared cancellation signal at node boundaries and inside long waits. Use cancellable waits instead of uninterruptible long sleeps. Cancellation stops new dispatches, records a terminal reason, and leaves a durable recovery checkpoint. Python threads cannot safely kill non-cooperative work; isolate untrusted/blocking work in a child process when hard termination is required.
4. **Bounded queues and concurrency.** Bound pending work, per-provider concurrency, parallel graph fan-out, and output sizes. Apply backpressure rather than spawning unbounded workers. Never wait for a future whose worker is blocked on the same saturated executor.
5. **Timeouts are not success.** Timeout, cancellation, lost lease, missing output, corrupt checkpoint, and provider discovery failure are explicit failure/unknown states. Do not accept late results from expired attempts.
6. **Safe retry.** Retries have a shared, durable attempt/time/cost budget, backoff with jitter, and a progress/evidence condition. No retry without new evidence or measurable state change. Reconcile an ambiguous external side effect before retrying; use task-scoped idempotency keys.
7. **Durable checkpoints.** Save state at meaningful phase boundaries and before/after consequential operations. On restart, compare task ledger, checkpoint, leases, and evidence receipts; reconcile deterministically or fail closed. Never manufacture a successful result after recovery failure.
8. **Non-blocking UI and API.** Dashboard requests read durable snapshots and return quickly. Long tasks run as background jobs with polling/streaming progress, cancellation, and reconnect. A dashboard client must not own the canonical run state.
9. **Bounded I/O.** Set connect/read/overall timeouts for network calls; cap response size and subprocess stdout/stderr; avoid unbounded reads, regexes, loops, lock waits, and filesystem scans.
10. **Diagnostics.** Every blocked/stalled task exposes its last completed stage, elapsed time, remaining budget, wait dependency, retry count, cancellation state, and recovery action. Logs must not include secrets or private prompts.

## Suggested starting budgets

These are defaults to tune by measured workload, not universal guarantees:

| Operation | Initial bound | Required behavior on expiry |
|---|---:|---|
| local control/metadata operation | 2–5 s | return an explicit error; no hidden retry |
| GitHub/API request | 10–30 s per request | bounded retry only for safe/idempotent reads |
| provider/model call | 30–120 s per attempt | cancel/terminate where possible; record timeout |
| validation subprocess | 60–600 s per command | terminate process group; retain bounded logs |
| phase | 5–10 min | checkpoint, cancel child work, report resumable state |
| whole task | explicit absolute deadline | checkpoint and end in timed-out/cancelled, never success |

A task that legitimately needs longer can use a larger explicit budget; it must still expose progress and cancellation. These defaults do not override stricter security policy or user deadlines.

## Acceptance tests

- [ ] A fake provider that never returns cannot block the supervisor beyond the configured deadline.
- [ ] A hung subprocess is terminated with its process group, and output capture remains bounded.
- [ ] Cancellation during idle, node execution, model inference, validation, and recovery is prompt and persisted.
- [ ] A timed-out worker cannot commit success after its lease/deadline expires.
- [ ] Restart preserves deadline and retry budget; retries do not reset on fresh context.
- [ ] A dispatch replay racing with the original request returns the same task for the same idempotency key and conflicts only for a genuinely different intent.
- [ ] Lock contention, a full queue, a provider outage, and a failed state store produce actionable bounded failures rather than a hang.
- [ ] Dashboard refresh/reconnect does not start duplicate work and continues to show the durable run status.
- [ ] Every timeout/failure test asserts both bounded completion time and correct terminal state; “it returned” alone is insufficient.

## Current caveat

AUREN already has deadline budgets, shared execution guards, cooperative cancellation, bounded daemon loops, graph timeouts, subprocess timeout support, and a dispatch intent ledger. These are useful controls but do not guarantee hard termination for every Python thread or every external provider. Host-specific adapters must honor deadlines, and hard-stop guarantees require process isolation. CI passing proves only the exercised cases; run the acceptance matrix above before claiming every step is hang-proof.
