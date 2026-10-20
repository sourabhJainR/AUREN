# Responsive Execution and Timeout Policy

**Release:** 1.0.5  
**Scope:** supervisor, graph nodes, provider/model adapters, subprocesses, repository operations, CI orchestration, dashboard sessions, and recovery handlers.

## Goal

No single step may hold the whole engineering workflow indefinitely. Responsiveness means bounded waiting, visible progress, cancellation, recoverability, and truthful terminal status—not merely returning quickly or hiding errors.

## Mandatory execution contract

1. **Deadline hierarchy.** Every run has an absolute deadline; each phase, provider/model call, subprocess, network request, lock acquisition, and validation command has a shorter local timeout. A child deadline may never extend its parent deadline. Validate timeout values as finite and positive.
2. **Progress heartbeat.** Persist the run/session ID, phase, task/attempt ID, last-progress timestamp, deadline, heartbeat/lease, and current wait reason. Report progress on state transitions or measurable work; heartbeat refreshes alone must not masquerade as progress.
3. **Cancellation and interruption.** Poll a shared cancellation signal at node boundaries and during long waits. Use cancellable waits instead of uninterruptible sleeps. Cancellation stops new dispatches, records a terminal reason, and leaves a durable recovery checkpoint. Python threads cannot safely kill non-cooperative work; isolate untrusted/blocking work in a child process when hard termination is required.
4. **Bounded queues and concurrency.** Bound pending work, per-provider concurrency, graph fan-out, and output sizes. Apply backpressure rather than spawning unbounded workers. Never wait for a future whose worker is blocked on the same saturated executor.
5. **Timeouts are not success.** Timeout, cancellation, lost lease, missing output, corrupt checkpoint, and provider discovery failure are explicit failure/unknown states. Late results from expired attempts cannot be accepted.
6. **Safe retry.** Retries share a durable attempt/time/cost budget, use backoff with jitter, and require new evidence or measurable progress. Reconcile an ambiguous external side effect before retrying; use task-scoped idempotency keys.
7. **Durable checkpoints.** Save state at meaningful phase boundaries and before/after consequential operations. On restart, compare task ledger, checkpoint, leases, and evidence receipts; reconcile deterministically or fail closed. Never manufacture success after recovery failure.
8. **Non-blocking UI and API.** Dashboard requests read durable snapshots and return quickly. Long tasks run as background jobs with polling/streaming progress, cancellation, and reconnect. The dashboard client never owns canonical run state.
9. **Bounded I/O.** Set connect/read/overall timeouts for network calls; cap response sizes and subprocess stdout/stderr; avoid unbounded reads, regexes, loops, lock waits, and filesystem scans.
10. **Diagnostics.** Every stalled task exposes its last completed stage, elapsed time, remaining budget, wait dependency, retry count, cancellation state, and recovery action. Logs must not contain secrets or private prompts.

## Suggested starting budgets

These are initial defaults to tune from measured workloads, not universal guarantees:

| Operation | Initial bound | Required behavior on expiry |
|---|---:|---|
| local control/metadata operation | 2–5 s | explicit error; no hidden retry |
| GitHub/API request | 10–30 s per request | bounded retry only for safe/idempotent reads |
| provider/model call | 30–120 s per attempt | cancel/terminate where possible; record timeout |
| validation subprocess | 60–600 s per command | terminate process group; retain bounded logs |
| phase | 5–10 min | checkpoint, cancel child work, report resumable state |
| whole task | explicit absolute deadline | checkpoint and end timed-out/cancelled, never success |

A task may request a larger explicit budget, but must still expose progress and cancellation. These defaults never override stricter security policy or a user's deadline.

## Executable acceptance matrix

The hard-stop primitive is portable.bounded_subprocess.run_bounded_command. It accepts argv rather than shell strings, inherits the parent monotonic deadline, observes cancellation, and caps retained stdout/stderr in memory while continuously draining pipes. Harness validation now uses this runner rather than subprocess.run(timeout=...), so a timeout can terminate the command's process group instead of only returning from the caller. Run: python -m unittest tests.test_bounded_subprocess_fault_injection tests.test_harness_task_deadline -v.

| Fault | Executable check | Required outcome |
|---|---|---|
| Hung subprocess | test_hung_command_is_terminated_within_budget_and_not_success; test_validation_timeout_terminates_hung_command | timed_out within bound |
| Parent deadline | test_parent_deadline_caps_child_timeout; test_parent_deadline_is_propagated_to_validation_subprocess | parent_deadline_exceeded |
| Cancellation | test_cancellation_terminates_command_and_is_explicit; test_pre_cancelled_validation_does_not_start_command | cancelled |
| Output flood | test_output_capture_is_bounded_and_marks_truncation; test_validation_output_capture_is_bounded_and_reports_truncation | bounded retained output and log |
| Descendant process | test_timeout_kills_descendant_process_group (POSIX) | no live descendant |
| Invalid budget/shell string | test_shell_strings_and_invalid_budgets_are_rejected | reject before spawn |

## Acceptance tests

- [ ] A provider that never returns cannot block the supervisor beyond the configured deadline.
- [x] A hung subprocess is terminated with its process group, and output capture remains bounded by the new fault-injection suite; cross-platform CI is still required.
- [ ] Cancellation during idle, node execution, inference, validation, and recovery is prompt and persisted.
- [ ] A timed-out worker cannot commit success after its lease/deadline expires.
- [ ] Restart preserves deadline and retry budget; fresh context does not reset retries.
- [ ] A dispatch replay racing with the original request returns the same task for the same idempotency key and conflicts only for genuinely different intent.
- [ ] Lock contention, a full queue, provider outage, and failed state store produce actionable bounded failures rather than a hang.
- [ ] Dashboard refresh/reconnect does not start duplicate work and still shows durable status.
- [ ] Every timeout/failure test asserts both bounded completion time and correct terminal state.

## Existing capability and limitation

AUREN already has deadline budgets, shared execution guards, cooperative cancellation, bounded daemon loops, graph timeouts, subprocess timeout support, and a durable dispatch-intent ledger. These controls are useful but do not guarantee hard termination for every Python thread or external provider. Host adapters must honor deadlines, and hard-stop guarantees require process isolation. Passing CI proves only the scenarios exercised by the tests; do not claim every step is hang-proof until the acceptance matrix is implemented and run.
