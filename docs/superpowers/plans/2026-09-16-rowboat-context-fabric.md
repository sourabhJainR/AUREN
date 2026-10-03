# Rowboat Context Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a durable, provenance-aware context graph using the existing AUREN memory database so future context can accumulate relationships without creating a second orchestration or memory owner.

**Architecture:** Add a small `ContextGraph` facade over the SQLite database already owned by `PersistentMemory`. `StateGraph`, `TaskPlan`, `CapabilityFabric`, and existing context packing remain unchanged. The graph is a bounded projection of durable context, not an execution engine.

**Tech Stack:** Python standard library, SQLite/WAL, pytest, existing AUREN portable runtime.

**Spec:** `docs/ROWBOAT_ADAPTATION_SPEC.md`

## Global Constraints

- No paid service or new third-party runtime dependency.
- `PersistentMemory` remains the sole durable memory owner.
- Graph traversal is deterministic and bounded.
- Graph records retain provenance, confidence, and timestamps.
- Existing orchestration and safety gates remain authoritative.

---

### Task 1: Durable Context Graph

**Files:**
- Create: `portable/context_graph.py`
- Create: `tests/test_context_graph.py`

**Interfaces:**
- `ContextNode(node_id, kind, label, source, confidence, properties)` is immutable.
- `ContextEdge(edge_id, source_id, relation, target_id, source, confidence, properties)` is immutable.
- `ContextGraph(memory, project)` exposes `upsert_node`, `get_node`, `link`, `neighbors`, and `digest`.
- `ContextGraph` uses `PersistentMemory.path` and the same SQLite database; it does not create a separate database or replace memory semantics.

- [x] Define immutable node/edge contracts with validation.
- [x] Add relationship tables and indexes to the existing SQLite database.
- [x] Add deterministic, bounded neighbor traversal for outgoing, incoming, and both directions.
- [x] Preserve node creation timestamps across updates.
- [x] Make relationship identifiers deterministic for the default edge identity.
- [x] Add stable graph digesting and persistence coverage.
- [x] Add budget, direction, relation-filter, duplicate-write, and persistence regression tests.

### Task 2: Review, CI, and merge Slice 1

**Files:**
- Review: PR diff and CI results only unless findings require source changes.

- [x] Open the PR against `main`.
- [x] Review the diff for ownership, determinism, bounds, security, and compatibility.
- [x] Fix actionable findings before merge.
- [ ] Wait for every required GitHub check to finish; do not stop at the first green check.
- [ ] Merge only after all checks are green and review findings are resolved.
- [ ] Start the next backlog slice from the newly merged `main`.

### Task 3: Context Resolver (next slice)

- Resolve task, workspace, memory, evidence, repository relationships, and prior run outcomes into the existing bounded `ContextPack`.
- Preserve token budgets and explicitly report omitted context.
- Do not bypass existing repository intelligence or verification boundaries.

### Task 4: Trigger / Background Run Contract (next slice)

- Generalize existing scheduler/event primitives into a durable trigger contract with event identity, deduplication, concurrency claims, bounded retries, and a handoff into the normal AUREN execution lifecycle.

### Task 5: Run Journal to Context Graph (next slice)

- Persist selected execution outcomes, decisions, discovered facts, verification receipts, review outcomes, and unresolved risks as graph facts with provenance.
- Keep learning candidates advisory until existing regression/shadow/canary gates approve them.

### Task 6: Workspace Collaboration Context (next slice)

- Add a bounded workspace scope over shared task context and graph relationships.
- Keep private context isolated unless explicitly promoted as an evidence-backed summary.
