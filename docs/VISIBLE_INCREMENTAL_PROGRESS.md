# Visible Incremental Progress and No-Silent-Stall Contract

AUREN must keep users informed from task acceptance through terminal outcome. A long task may take time; an unobservable task is a runtime defect.

## Required behavior

- Persist append-only progress events with stable task ID, sequence, timestamp, current stage, concise factual message, and evidence reference where available.
- Publish events when a task is accepted, a stage starts or completes, a meaningful unit completes, tests start or finish, a provider is dispatched, recovery begins or ends, a task blocks or unblocks, approval is needed, cancellation occurs, and the task reaches a terminal state.
- Report percentage only when the executor supplies a meaningful numerator and denominator. Never estimate progress from elapsed time, token count, planned steps, or optimism.
- Heartbeats prove worker activity but do not increase completion. Consumers must distinguish running, blocked, recovering, stale, failed, cancelled, and completed.
- A stale heartbeat must trigger a visible warning and bounded supervisor action: inspect worker health, deadline, cancellation and checkpoint; resume or retry only within policy; otherwise fail with the reason and recovery instructions.
- Drain long subprocesses and provider streams incrementally. Persist progress before long waits. Do not hold UI/API request threads while work executes; expose polling or streaming from the durable journal.
- Store summaries and evidence references, not raw prompts, secrets, full tool payloads or private model context. Redaction is defense in depth.
- On restart, rebuild snapshots from the journal and resume only from verified checkpoints. Do not reset progress, claim success after failed recovery, or replay non-idempotent effects without ledger reconciliation.
- Keep status, cancellation, deadlines and health responsive while workers are blocked. Enforce hard process termination when cancellation/deadline expires.
- Every stage must yield progress, a visible blocked state with reason and next action, or a terminal result. No indefinite silent waiting.

## Acceptance matrix

1. Real stage/unit transitions produce durable events; sequence is monotonic and snapshots survive restart.
2. Percentage is absent unless work has explicit measurable units; heartbeat never changes it.
3. Stale non-terminal work surfaces last-update age and a recovery/failure next action.
4. Provider outage/timeout remains visible while fallback or retry occurs.
5. Cancellation/deadline keeps control responsive and reflects actual worker exit.
6. Restart reconciles journal and checkpoint without duplicate side effects or false completion.
7. Progress output redacts credential-like values and excludes raw prompts/tool contents.
8. Dashboard, CLI, API and notifications consume the same canonical task state.

A standalone journal is a foundation, not proof that every worker, dashboard or provider is integrated. Keep integration gaps explicit until end-to-end tests demonstrate them.
