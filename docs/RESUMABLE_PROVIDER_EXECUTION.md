# Resumable provider execution

Provider-backed graph stages can fail over across operator-registered, version-pinned providers without losing the durable task identity. The executor receives a canonical task-scoped `TaskRetryBudget`, writes every transition through the supervisor-provided checkpoint callback, and emits a digest-bound evidence event through the evidence callback.

## Recovery behavior

- Connection reset, timeout, and transient provider failures try another registered provider with the same stable task/stage idempotency key.
- HTTP 429 responses checkpoint the provider reset time. If every registered provider is rate-limited, execution returns `deferred` plus `resume_at`; the scheduler resumes after that time rather than spinning or discarding work.
- A restart restores retry charges monotonically. The caller can change the preferred provider or remove an unavailable provider; unknown old cooldown entries are ignored, while task/stage identity and checkpoint digest remain mandatory.
- Provider version/configuration digests and checkpoint digests are recorded with evidence. Credentials and prompts are not persisted in error metadata.
- The shared retry budget bounds provider switching alongside other graph/crew retries. Exhaustion blocks rather than looping.

## Limits and safety

The provider executor is a checkpointable runtime component and its persistence/evidence callbacks must be wired to the canonical supervisor graph checkpoint and evidence ledger in production. It does not claim transport-level exactly-once execution. Provider calls should be proposal/read-only operations; irreversible effects require durable outbox/inbox semantics and downstream idempotency enforcement. Rate-limit recovery is scheduler-driven; this module does not sleep or run background timers.

## Bounded waits and silent-stall prevention

The executor bounds how long the supervisor waits for each provider call (default 120 seconds; hosts may configure a stricter finite positive value) and emits provider attempt/start/completion/failure/timeout events through an optional progress callback. A hung call returns an explicit `timed_out` result with `late_result_possible=true`; it does not fail over or retry automatically because the worker may still finish after the timeout. This is a responsiveness guard, not a way to kill a Python thread. Use provider-native transport deadlines and process isolation where hard termination is required. Reconcile the attempt before retrying, and do not use this thread wrapper for irreversible side effects.

Regression tests: `test_hung_provider_returns_bounded_unknown_and_never_fails_over` and `test_invalid_provider_call_timeout_rejected`.

## Shared provider-call boundary

The resumable executor and `ProviderBackedCrewAdapter` now use the same bounded provider-call helper and share an eight-worker cap. A host invoker that ignores its timeout can no longer hold the calling graph node indefinitely. The caller receives an explicit timeout; the late worker is daemonized and consumes one of the bounded slots until it exits. A timed-out attempt must be reconciled before retry. This still does not kill the worker or make a provider-side operation exactly-once; configure native transport deadlines and use process isolation for hard termination.

Regression coverage: `test_hung_provider_is_bounded_even_if_invoker_ignores_timeout` and the resumable executor timeout/no-failover tests.


## Wiring provider lifecycle events into the canonical task journal

When registering provider-backed specialist crews, pass the exact `ProgressJournal` instance owned by the execution supervisor:

```python
adapters = runtime.register_provider_backed_crew_adapters(
    provider_invokers=provider_invokers,
    provider_ids=provider_ids,
    provider_versions=provider_versions,
    tools=approved_tools,
    allowed_tools_by_crew=allowed_tools_by_crew,
    progress_journal=controller.progress_journal,
    replace=True,
)
```

The registration API wires the journal's allowlisted callback to every specialist crew; do not create a second journal for telemetry. The graph dispatch must propagate the supervisor-issued task ID. `portable.state_graph.GraphAgentTeam.run()` receives that ID directly; the alternate `.ai-harness/runtime/graph_agent_team.py` path accepts `supervisor_task_id` on `execute()`. If provider callbacks are enabled but the task ID is missing, the adapter fails closed instead of recording an un-attributed event. Never substitute a model-generated task ID. This makes provider progress observable, but does not make timed-out Python threads killable or resolve ambiguous external effects by itself.


## Startup reconciliation after a process interruption

PR [#777](https://github.com/sourabhJainR/AUREN/pull/777) reconciles progress projections on `ExecutionController` startup using both the durable task row and executive work-unit state. A task is projected as `completed`, `failed`, or `cancelled` only when both stores agree on that terminal state. If the task row is terminal but its receipt is still running/missing—or if a non-terminal task conflicts with a terminal receipt—the journal projects `blocked` and health findings require reconciliation. Running tasks with fresh lease and heartbeat remain running; missing or expired lease metadata projects `recovering`. The journal appends a `startup_reconciliation` event rather than deleting history, and this explicit correction path is the only one allowed to supersede a terminal projection. Reconciliation uses an expected-sequence check under the journal write lock; if another worker appended progress after the scan, the correction is rejected and a health finding remains for retry. Reconciliation never reruns a handler or assumes an external side effect did not happen. The task scan is bounded; if it is incomplete, rerun `controller.reconcile_progress_journal(limit=..., time_budget_seconds=...)` with a larger bound before trusting unscanned projections.

## Hard bounds for the security-gated harness bridge

The `.ai-harness/safe_provider.py` bridge applies a process-level bound around `provider.py`, in addition to the provider invoker's own timeout and response limit.

- `HARNESS_PROVIDER_TIMEOUT_SECONDS` defaults to 1800 seconds and must be within 1–3600 seconds.
- `HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS` defaults to 3600 seconds and must be within 1–7200 seconds. All attempts and retry backoff share this one total budget; a retry never resets it.
- If `AUREN_DEADLINE_EPOCH` is set to an absolute Unix timestamp, the bridge caps its total budget at that inherited supervisor deadline. An expired deadline prevents launch and returns exit code `124`.
- `SubprocessProviderInvoker` propagates an absolute `AUREN_DEADLINE_EPOCH` to its child. It uses the earlier of its own timeout and any inherited deadline, rejects invalid/expired deadlines before launch, and rejects non-finite or greater-than-24-hour timeout values.
- Canonical `AUREN_` settings take precedence. The documented unprefixed `HARNESS_` settings are supported, with legacy `AER_` values as fallback.
- `HARNESS_PROVIDER_MAX_OUTPUT_BYTES` defaults to 1,000,000 bytes and must be within 1,024–16,000,000 bytes.
- Output is read as fixed-size byte chunks through a bounded queue. The bridge does not use line-based reads, so a provider cannot force a single unbounded line allocation.
- Timeout terminates the contained process group/job and returns exit code `124`. Output overflow or invalid bound configuration terminates or rejects the invocation and returns exit code `125`. Neither condition is treated as successful provider output or automatically retried by this bridge.
- A normal parent exit also triggers descendant cleanup before the bridge returns. Windows uses the existing kill-on-close Job Object helpers; POSIX uses the isolated process group. Regression tests cover bounded output, overflow, hard deadlines, descendant cleanup, and invalid configuration.

These controls bound output retained by the bridge. They do not prove that a remote provider did not accept an external operation before interruption. Consequential operations still require the durable side-effect ledger, provider-native idempotency/lookup, and trusted receipt reconciliation described in `PROVIDER_OPERATION_LIFECYCLE.md`.

### Rollback and recovery

If a supported provider needs a compatibility fix, keep the process deadline, output cap, and process-tree cleanup enabled. Adjust only the documented finite limits within their accepted ranges, then rerun the cross-platform regression matrix. Do not roll back to unbounded line collection or an unbounded `wait()`. If this bridge integration must be reverted, first provide an equivalent bounded process boundary in the replacement path; otherwise disable consequential provider dispatch and leave the operation blocked for reconciliation. Rerun the output-overflow, timeout, descendant-cleanup, invalid-configuration, provider-lifecycle, and exact-head CI suites before restoring writes.

