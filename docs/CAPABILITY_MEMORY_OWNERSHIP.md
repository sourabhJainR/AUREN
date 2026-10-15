# Canonical Capability and Memory Ownership

AUREN has one owner for capability semantics and one owner for durable memory semantics.

## Ownership

| Concern | Canonical owner | Compatibility surfaces |
|---|---|---|
| Capability catalog, risk, fallback and provider resolution | `portable.agent_capabilities` | `portable.capability_fabric`, agency imports |
| Durable memory, scoping, approval, redaction and FTS recall | `portable.agent_capabilities.PersistentMemory` | `portable.persistent_memory`, agency `MemoryStore` |
| Memory-kind routing, bounded recall, task-local working/sensory state and resource telemetry | `portable.memory_orchestrator.MemoryOrchestrator` | context consumers and composition roots |
| Memory provenance and hierarchy relationships | `portable.context_graph.ContextGraph` on the same canonical SQLite file | memory orchestration and evidence lineage |
| Reusable prompt/KV-style pages | `.ai-harness/runtime/context_cache.py:ContextPageCache` | memory orchestration and context pipeline |
| Verified procedural learning and post-run reflection | `.ai-harness/runtime/task_memory.py` and `portable.dream_memory.DreamMemory` | callbacks registered with `MemoryOrchestrator` |
| Agency tool metadata and collaboration | `portable.agency_agent_capabilities` | agency callers only |
| Repository semantic retrieval | `portable.agency_codebase_context.CodebaseIndex` | `portable.repository_intelligence` |
| AI repository packing | `portable.repository_intelligence.RepositoryIntelligence` | agent/context callers |

Compatibility modules may adapt APIs, but they must not create a second catalog, second durable store, or competing policy.

## Capability rule

`CapabilityFabric` is authoritative for what a capability means, its risk, sandbox/network requirements, fallback and provider selection. Provider adapters transport execution; they do not redefine capability semantics.

## Memory rule

`PersistentMemory` is authoritative for durable memory. Agency `MemoryStore` is a compatibility adapter for the older key/value API. It translates legacy `MemoryFact` objects into canonical records and never maintains a second JSONL store.

Memory remains scoped, redacted, approval-aware and evidence-oriented. A memory entry is not proof of correctness.

## Retrieval rule

Repository intelligence is layered:

1. semantic/indexed retrieval for code structure and relationships;
2. text retrieval for non-code and free-form questions;
3. bounded packing for tasks that need broader repository context.

The semantic layer follows Serena's useful pattern of stable symbol/relationship addressing and project-scoped indexing. The packer follows Repomix's useful pattern of git-aware exclusion, secret filtering, deterministic packing, token accounting and structural compression. Neither dependency is required by the AUREN core.

For small edits, normal text/patch tools remain preferred. Semantic tooling is most useful for symbol discovery, references, hierarchy, cross-file refactoring and bounded structural context.

## Change policy

New capability or memory behavior must first extend the canonical owner. Do not add another `*Capabilities`, `*MemoryStore`, `*SecondBrain`, or provider-specific persistent store with overlapping responsibility.

When an older API must remain, implement it as a thin adapter and add a conformance test proving that it delegates to the canonical owner.


## Memory orchestration and resource policy

Use `portable.memory_orchestrator.MemoryOrchestrator` as the routing facade, not as a new store. It delegates durable records to `PersistentMemory`, provenance to `ContextGraph` in the same SQLite database, and reusable prompt pages to the existing `ContextPageCache`. The caller may bind procedural, reflective, hierarchical or prospective writes to existing task-memory, DreamMemory, graph and ExecutionController owners through explicit callbacks.

| Memory type | Lifetime / canonical route | Safety rule |
|---|---|---|
| Episodic | Durable, project- and intent-scoped `PersistentMemory` + graph provenance | Keep evidence IDs and source in graph metadata; memories are not proof |
| Semantic | Durable `PersistentMemory` + graph provenance | Rank verified and relevant facts; retain scope |
| Working | Bounded task-local process memory | Disposable, scoped to task; never silently promote |
| Procedural | Existing learning-ledger callback when wired; otherwise approved canonical durable memory | Verified promotion requires evidence |
| Sensory | Short-TTL bounded process memory | Expire lazily on access; no background polling |
| Reflective | DreamMemory/learning callback when wired; otherwise approved canonical durable memory | Reflection does not auto-change policy or executable code |
| Hierarchical | ContextGraph relationships in the canonical memory database | No duplicate graph database |
| Prospective | ExecutionController/work queue callback when wired; otherwise a non-executable approved intention record | A memory note must never masquerade as a scheduled task |
| Meta-memory | Read-only bounded counters and cache/recall metrics | Do not recursively persist telemetry as memories |
| KV-style cache | Existing content-addressed `ContextPageCache` | Process-local; not durable memory or a provider GPU KV cache |

Retrieval must obey a hard character budget, use task/intent scope, prioritize required/verified/relevant items, and fail closed if required memory is missing or does not fit. Idle reads and metrics must not invoke a model or provider. Eviction and TTL apply only to disposable process-local state; durable records, graph lineage, evidence receipts and rollback data are not evicted by the coordinator. Use the repository's standard `unittest` patterns. Passive dataclass DTOs may use the established class-level `# pragma: no cover` pattern; behavior belongs in the orchestrator and must be tested.
