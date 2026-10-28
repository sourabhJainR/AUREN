# Bounded context and resumable large-input processing

AUREN treats the model context as a bounded working set. The original input and verified artifacts remain outside the prompt; work is streamed in deterministic, source-digest-bound chunks and progress is written to a durable SQLite journal.

## Runtime contract

- Use `portable.context_streaming.iter_file_chunks` for UTF-8 files. It reads incrementally and emits bounded chunks with stable offsets and SHA-256 digests. Overlap preserves boundary context; callers must avoid double-counting overlap in aggregations.
- Use `file_sha256` to bind a job to the exact source bytes. A changed source must start a new job ID; never resume old results against changed input.
- Use `DurableChunkJournal` to persist completed chunk results before moving on. Recording the same digest/result is idempotent; conflicting replay is rejected. Keep result strings small and store large artifacts separately with digest references.
- Sampling is opt-in through `choose_sample(..., exploratory=True)`; verification mode always rejects sampling. Security checks, acceptance tests, sealed holdouts, policy checks and independent evaluation must process full required coverage.
- Summaries are navigation aids, not replacement evidence. Keep source digest, chunk digest, offsets, findings and artifact references so a fresh worker can retrieve the original material.
- Long-running work should be a durable identity with short-lived execution attempts: checkpoint, yield, wake, reconcile state and continue. Do not rely on in-memory timers or an always-running process for months. Use the existing AUREN supervisor, execution guard, cancellation, finite budgets and side-effect ledger; this module does not replace them.

## Adaptive strategy

Before a limit is hit, select the least-lossy safe strategy: stream; chunk at structural boundaries; batch independent chunks under a shared budget; retrieve only relevant ranges; offload bulky outputs to digest-addressed artifacts; compact a handoff with links to canonical evidence; then resume in a fresh context. Deterministic sampling is allowed only for explicitly exploratory work. If no safe strategy satisfies the required coverage, record a blocker rather than truncate data or weaken Guard.

## Rollback and evaluation

This module is additive. Rollback by removing its callers and reverting the module, tests and this document together. Validate chunk boundary integrity, Unicode, source drift, idempotent recovery, conflicting replay and sampling denial. CI does not prove production storage durability or month-long unattended operation; those require deployment-level restart, retention, alerting, budget and rollback rehearsals.
