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

The registration API wires the journal's allowlisted callback to every specialist crew; do not create a second journal for telemetry. The graph dispatch must propagate the supervisor-issued task ID. `portable.state_graph.GraphAgentTeam.run()` receives that ID directly; the alternate `.ai-harness/runtime/graph_agent_team.py` path accepts `supervisor_task_id` on `execute()`. If provider callbacks are enabled but the task ID is missing, the adapter fails closed instead of recording an un-attributed event. Never substitute a model-generated task ID. This makes provider progress observable, but does not make timed-out Python threads killable or resolve cross-store crash recovery by itself.
