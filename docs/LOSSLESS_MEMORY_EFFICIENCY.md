# AUREN lossless memory efficiency

**Status:** initial implementation slice; exact-head CI and benchmark results are required before promotion. This is not a claim that all durable memory storage has been compressed.

## Integration, not an island

The public `portable.persistent_memory.PersistentMemory` facade remains over the existing canonical `portable.agent_capabilities.PersistentMemory`. The `context_chunks()` method retrieves from that canonical store and returns bounded, source-linked chunks. `compact_snapshot()` produces a reversible compressed snapshot without mutating canonical memory. No new database, vector store, scheduler, policy engine, model dependency, or orchestrator is introduced.

## What to borrow from TurboQuant

TurboQuant is a vector-quantization technique developed for high-dimensional vectors, including model key/value caches and vector search. Its rotation, scalar quantization, and residual correction target geometric distortion in numeric vectors; it is not a lossless text compressor. AUREN's current durable memory stores text records and uses SQLite/FTS5, so applying vector quantization directly to those records would be a category error.

AUREN should evaluate TurboQuant-like quantization only if a measured workload establishes that embedding or model KV-cache memory is a material bottleneck. Keep source text, policy, acceptance criteria, provenance, negative evidence, approval receipts, and recovery records exact. A future approximate-vector index must be a rebuildable retrieval projection over canonical records, with exact-source fallback and a feature flag.

Reference: Google Research, [TurboQuant: Redefining AI efficiency with extreme compression](https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/); paper [arXiv:2504.19874](https://arxiv.org/abs/2504.19874). Published claims are research leads, not AUREN benchmark results.

## Implemented primitives in this slice

- Deterministic zlib snapshots over canonical JSON, with schema version, record count, and SHA-256 integrity check. The operation is reversible and does not delete or mutate source records.
- Bounded context chunking with stable record IDs and per-chunk digest. Long records carry a source-record digest and numbered part markers.
- Security, policy, constraints, requirements, acceptance, rollback, invariant and negative-evidence categories are ordered before ordinary lessons by default. This is ordering, not permission to omit lower-priority records.
- `context_chunks()` is wired into the existing `PersistentMemory` facade and accepts the same project, intent, branch and source-revision scopes.
- Invalid/corrupt compressed snapshots fail closed. Chunks are bounded by character count, not tokenizer tokens; the caller must still enforce provider-specific token limits.

## Non-goals and guardrails

- Do not summarize or truncate canonical memories as a side effect of context assembly.
- Do not treat a digest as proof of source authenticity or independent verification.
- Do not silently omit required records when the budget is tight. Process every returned chunk or record explicitly which chunk was not consumed.
- Do not quantize textual policy, security, acceptance, provenance, negative evidence or recovery state.
- Do not enable approximate retrieval by default, or claim a memory-size reduction until measured against real data.
- A successful zlib snapshot only proves reversible packing of the supplied records; it does not compact the SQLite database itself.

## Required benchmark before any default enablement

Compare baseline retrieval/context assembly against the candidate using a pinned dataset and paired runs. Record stored bytes, compressed bytes, peak RSS, retrieval latency, context-build latency, end-to-end task success, acceptance-criterion recall, critical-failure/negative-evidence recall, source attribution, security-policy violations, and uncertainty. Include long-tail records, Unicode, duplicates, contradictions, retractions, stale revisions, adversarial instructions, and oversized inputs. The required threshold for missed critical facts and policy constraints is zero; any failure blocks promotion. Report compression ratio separately from retrieval quality.

## Rollback

Disable `context_chunks()` at the call site and return to ordinary canonical `search()`. The compressed snapshot is optional and source memory is untouched. Remove the helper only after call sites are migrated and regression coverage is preserved. Any future storage-schema compression needs a separate migration with round-trip validation, old-version readability, crash recovery and a tested rollback before deleting or rewriting canonical records.
