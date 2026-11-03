# Memory lifecycle

The memory system is deliberately split into three layers so execution context does not rot.

## 1. Private working memory

`portable.agent_memory.AgentMemory` belongs to one agent and one run. It is bounded, disposable working state. `ProfileAgentMemory` resolves a versioned profile from the canonical registry and partitions the file by profile ID, revision, profile digest, agent ID and run ID. It checks the identity metadata on each read. Graph phase agents select their profile as a memory partition only; this does not grant role or tool authority. Both forms are private working state and are never implicitly promoted to team knowledge.

## 2. Shared task memory

`SharedTaskMemory` contains only bounded handoffs needed by the current dependency graph. It is not a transcript. Old entries are evicted and the context engine selects only task-relevant items.

## 3. Durable learning ledger

`.ai-harness/runtime/task_memory.py` is the durable, portable backend. SQLite is used from the Python standard library with WAL, transactions, busy timeouts and a process lock. An append-only JSONL audit file preserves the full recorded observations.

Every observation carries a schema version, learning version, revision, task, outcome, category, approach, detail, command, run, source agent, evidence and promotion state. `candidate` is the safe default. `verified` is reserved for curated knowledge.

## Dreaming

Dreaming is a post-run lifecycle, not an execution step. `portable.dream_memory.DreamMemory` reads observations after the execution graph has finished and curates repeated outcomes. The analysis never enters the execution prompt.

A repeated successful approach can become verified reusable guidance. Repeated failed/regressed approaches can become verified anti-patterns. Conflicting outcomes remain candidates and are not promoted.

The promotion threshold is deterministic and model-independent. Better models can improve the quality of the observations and the steward's analysis without changing the memory safety boundary.

## Guardrails

- Execution agents focus on the task, not memory administration.
- Private memory is run-scoped and bounded.
- Working context is bounded before it reaches any model.
- Durable learning is not copied from raw transcripts.
- Candidate learning is not trusted as verified truth.
- Conflicting evidence is not promoted.
- Durable writes are concurrency-safe and idempotent.
- The audit trail is append-only; curated learning can be superseded rather than silently rewritten.
- Repository files, tests and other source evidence remain the source of truth.
- Dreaming can become richer over time without increasing the execution prompt size.

## Improvement loop

```text
Run N
  -> execute focused task
  -> capture evidence/outcome
  -> steward records candidate observations
  -> dream cycle analyzes after execution
  -> repeated evidence becomes verified learning

Run N+1
  -> retrieve only relevant verified learning + relevant failures
  -> execute with a better starting point
  -> validate inherited learning
  -> record new evidence
  -> dream again
```

This makes improvement cumulative while keeping execution context small.
