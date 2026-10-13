# Rowboat Adaptation Specification

## Goal

Adapt useful Rowboat constructs into AUREN without creating a second orchestration engine, second memory owner, or privileged execution path.

## Architecture rules

1. `StateGraph` remains the execution authority.
2. `TaskPlan` remains the dependency authority.
3. `CapabilityFabric` remains the capability, risk, fallback, and provider authority.
4. `PersistentMemory` remains the durable memory owner.
5. Repository intelligence remains owned by the existing repository-map/context layers.
6. New context relationships must be persisted through the canonical durable store, not a parallel database.
7. Background/event automation must enter the existing orchestration path rather than directly executing tools.
8. Context is advisory input; verification, review, regression, canary, promotion, and security gates remain authoritative.
9. All new state must be deterministic, bounded, provenance-aware, and serializable.
10. No paid service or Rowboat runtime dependency is introduced.

## Incremental backlog

### Slice 1 — Durable Context Graph

Add a relationship-aware graph projection backed by the existing `PersistentMemory` SQLite database. Nodes and edges carry stable identifiers, provenance, confidence, timestamps, and deterministic serialization/digests. Provide bounded neighbor traversal without changing graph execution semantics.

### Slice 2 — Context Resolver

Resolve task, workspace, memory, evidence, repository relationships, and prior run outcomes into the existing bounded `ContextPack`. Preserve token budgets and explicitly report omitted context.

### Slice 3 — Trigger / Background Run Contract

Generalize the existing scheduler/event primitives into a durable trigger contract with event identity, deduplication, concurrency claims, bounded retries, and a handoff into the normal AUREN execution lifecycle.

### Slice 4 — Run Journal to Context Graph

Persist selected execution outcomes, decisions, discovered facts, verification receipts, review outcomes, and unresolved risks as graph facts with provenance. Keep learning candidates advisory until existing regression/shadow/canary gates approve them.

### Slice 5 — Workspace Collaboration Context

Add a bounded workspace scope over shared task context and graph relationships. Keep private context isolated unless explicitly promoted as an evidence-backed summary.

## Explicit non-adoptions

AUREN will not import Rowboat's desktop UI, Harbor server, hosted authentication, browser client, voice stack, commercial integrations, or provider-specific execution paths. These are product-surface concerns rather than orchestration primitives.

## Acceptance criteria

- Existing orchestration behavior remains unchanged for tasks that do not use the new constructs.
- No new runtime dependency is required.
- New graph state uses the existing memory database and lifecycle.
- Graph writes are validated and bounded.
- Relationship traversal is deterministic.
- Provenance and confidence are retained.
- Tests cover persistence, duplicate writes, bounds, ordering, and digest stability.
