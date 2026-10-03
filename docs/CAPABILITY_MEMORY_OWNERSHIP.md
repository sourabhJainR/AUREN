# Canonical Capability and Memory Ownership

AUREN has one owner for capability semantics and one owner for durable memory semantics.

## Ownership

| Concern | Canonical owner | Compatibility surfaces |
|---|---|---|
| Capability catalog, risk, fallback and provider resolution | `portable.agent_capabilities` | `portable.capability_fabric`, agency imports |
| Durable memory, scoping, approval, redaction and FTS recall | `portable.agent_capabilities.PersistentMemory` | `portable.persistent_memory`, agency `MemoryStore` |
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
