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

## Process-isolated crew dispatch

For trusted, importable proposal handlers that may block or ignore cancellation, use `portable.process_isolated_crew_dispatch.ProcessIsolatedCrewDispatcher`. It uses a spawned child process, a shared monotonic batch deadline, parent-observed cancellation, a 256 KiB default request cap, bounded result envelopes, and terminate-then-kill escalation. Each item returns an explicit `completed`, `failed`, `timed_out`, `cancelled`, or `not_run` status. It is a liveness boundary, not a sandbox or authorization boundary; handler code and credentials still need a restricted host. The existing thread-based `BoundedCrewDispatcher` remains compatible but cannot forcibly stop a non-cooperative thread.

Run: `python -m unittest tests.test_process_isolated_crew_dispatch -v`.

## Executable acceptance matrix

The hard-stop primitive is portable.bounded_subprocess.run_bounded_command. It accepts argv rather than shell strings, inherits the parent monotonic deadline, observes cancellation, and caps retained stdout/stderr in memory while continuously draining pipes. Harness validation and revision-bound evidence checks use this runner rather than relying on caller-side timeouts alone. POSIX commands run in a dedicated process group; Windows commands are assigned to a kill-on-close Job Object before resume so descendants are contained. Run: python -m unittest tests.test_bounded_subprocess_fault_injection tests.test_harness_task_deadline -v.

| Fault | Executable check | Required outcome |
|---|---|---|
| Hung subprocess | test_hung_command_is_terminated_within_budget_and_not_success; test_validation_timeout_terminates_hung_command | timed_out within bound |
| Parent deadline | test_parent_deadline_caps_child_timeout; test_parent_deadline_is_propagated_to_validation_subprocess | parent_deadline_exceeded |
| Cancellation | test_cancellation_terminates_command_and_is_explicit; test_pre_cancelled_validation_does_not_start_command | cancelled |
| Output flood | test_output_capture_is_bounded_and_marks_truncation; test_validation_output_capture_is_bounded_and_reports_truncation | bounded retained output and log |
| Descendant process (POSIX) | test_timeout_kills_descendant_process_group | no live descendant |
| Descendant process (Windows) | test_timeout_kills_windows_job_descendants | delayed child side effect never occurs |
| Invalid budget/shell string | test_shell_strings_and_invalid_budgets_are_rejected | reject before spawn |
| Crash after side-effect claim | test_crash_after_claim_requires_explicit_no_effect_retry_authorization | reconciliation required; retry needs trusted no-effect evidence |
| Crash after provider accepts effect | test_crash_after_provider_accept_reconciles_without_redispatch | reconcile provider receipt; never auto-redispatch |
| Evidence check hangs | test_default_runner_hard_stops_hung_command | TIMEOUT, no success |
| Total deadline exhausted | test_default_runner_inherits_total_deadline_between_checks | later checks NOT_RUN |
| Pre-cancelled evidence gate | test_pre_cancelled_evidence_gate_never_spawns_check | CANCELLED, no spawn |
| Non-cooperative crew handler | test_hung_handler_is_hard_stopped_within_deadline | timed_out within bound |
| Crew cancellation | test_cancellation_stops_worker_and_does_not_claim_success | cancelled, no result |
| Oversized crew request | test_request_size_is_bounded_before_worker_spawn | reject before spawn |
| Oversized crew result | test_result_size_is_bounded | failed, bounded error |
| Batch deadline | test_batch_deadline_prevents_later_dispatch | remaining tasks not_run |

## Crash-recovery acceptance matrix

The checkpoint store rejects reuse of an existing `(episode_id, sequence)` identity with a different state or payload. The fault-injection suite uses spawned child processes that exit without cleanup to verify committed checkpoint survival and rollback of uncommitted writes after restart.

Run: `python -m unittest tests.test_crash_recovery_fault_injection -v`.

| Fault | Executable check | Required outcome |
|---|---|---|
| Process exits after checkpoint commit | `test_committed_checkpoint_survives_abrupt_process_exit_and_restart` | committed state and digest survive restart |
| Process exits inside an uncommitted SQLite transaction | `test_uncommitted_checkpoint_is_not_visible_after_process_crash` | incomplete state is absent after restart |
| Duplicate checkpoint with same content | `test_identical_checkpoint_retry_is_idempotent_across_payload_key_order` | same durable checkpoint returned |
| Same sequence, different payload or state | `test_same_checkpoint_identity_with_different_payload_fails_closed`, `test_same_checkpoint_identity_with_different_state_fails_closed` | explicit conflict, original preserved |
| Corrupt stored payload | `test_corrupt_stored_checkpoint_is_rejected` | integrity error, no resume |
| Invalid sequence/non-finite payload | `test_invalid_sequences_and_non_finite_payloads_are_rejected` | reject before commit |

## Acceptance tests

- [ ] A provider that never returns cannot block the supervisor beyond the configured deadline.
- [x] A hung subprocess is terminated within budget, and output capture remains bounded by fault-injection tests on Linux/Windows and Python 3.11/3.12.
- [x] Windows subprocess descendants are assigned to a kill-on-close Job Object; a delayed-side-effect fault test passes on Windows/Python 3.11 and 3.12.
- [ ] Cancellation during idle, node execution, inference, validation, and recovery is prompt and persisted.
- [ ] A timed-out worker cannot commit success after its lease/deadline expires.
- [ ] Restart preserves deadline and retry budget; fresh context does not reset retries.
- [ ] A dispatch replay racing with the original request returns the same task for the same idempotency key and conflicts only for genuinely different intent.
- [ ] Lock contention, a full queue, provider outage, and failed state store produce actionable bounded failures rather than a hang.
- [ ] Dashboard refresh/reconnect does not start duplicate work and still shows durable status.
- [ ] Every timeout/failure test asserts both bounded completion time and correct terminal state.

## Existing capability and limitation

AUREN already has deadline budgets, shared execution guards, cooperative cancellation, bounded daemon loops, graph timeouts, subprocess timeout support, and a durable dispatch-intent ledger. These controls are useful but do not guarantee hard termination for every Python thread or external provider. Host adapters must honor deadlines, and hard-stop guarantees require process isolation. Passing CI proves only the scenarios exercised by the tests; arbitrary provider calls and in-process graph nodes still need deadline/cancellation adapters, and external side effects still need reconciliation.
