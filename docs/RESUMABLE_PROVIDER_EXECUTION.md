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
