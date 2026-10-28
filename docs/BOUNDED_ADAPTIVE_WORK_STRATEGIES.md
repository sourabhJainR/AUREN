# Bounded adaptive work strategies

**Status:** implementation slice; exact-head CI and downstream integration required.  
**Stable work key:** `AUREN:issue-696:continuous-engineering`

AUREN should respond to approaching context, payload, time, and concurrency limits by changing the *shape* of work before a hard failure—not by bypassing Guard or quietly reducing correctness.

## Strategy order

1. **Direct** — use one operation when measured estimates fit the configured limits.
2. **Chunk** — split text or artifacts at stable boundaries; keep source offsets/revisions and preserve complete coverage. Add overlap only where cross-boundary context is needed, and deduplicate findings by stable source identity.
3. **Batch** — group independent items into bounded batches. Bound both item count and payload size; preserve dependency ordering and stop dependents after a failed batch.
4. **Stream** — process incrementally with a bounded working set; checkpoint the last verified cursor and make resume idempotent.
5. **Sample** — only for explicitly exploratory, non-critical work. Record the seed, population count, sample size, and the fact that coverage is incomplete.
6. **Stop safely** — if no strategy preserves required coverage, surface a budget blocker and request a smaller scope or a capable adapter. Never claim partial work is complete.

The initial reusable helpers are in `portable/bounded_work.py`. They select an explicit plan, split text without dropping characters, batch lazily, and implement deterministic reservoir sampling with O(sample-size) memory.

## Guardrails that remain hard

- Sampling is prohibited when complete coverage is required, including security/policy checks, acceptance criteria, sealed holdouts, and independent verification.
- Chunking a protocol frame does **not** make a truncated JSON-RPC response valid. Oversized MCP frames remain rejected, with bounded prefix diagnostics; AUREN must use a provider's supported pagination/streaming interface in a separate, explicitly authorized read request rather than accept truncated output.
- Never automatically retry a consequential call after an unknown remote outcome. A smaller request is a new request only when its operation semantics and authorization are explicit.
- Never drop failures, constraints, citations, provenance, or negative evidence to fit a context window. Summarize with links/digests to retained source evidence, and verify the compressed context against the task contract.
- Resource fallback does not relax permission scopes, rate limits, timeouts, output bounds, approval, holdout custody, or OS isolation.

## Regression and rollback

`tests/test_bounded_work.py` covers coverage-preserving plans, no-sampling for required coverage, chunk boundaries, lazy batches, cap failures, and deterministic sampling. Run it with the repository's test conventions and require exact-head CI before merge.

Rollback by reverting the new helper, tests, and this document together. The helpers are opt-in and do not change existing MCP frame limits or automatically activate any provider/skill.
