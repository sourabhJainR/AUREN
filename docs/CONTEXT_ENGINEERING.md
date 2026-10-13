# Context engineering, memory and task handoffs

AUREN treats context as a bounded resource, not a transcript to forward between agents.

## Memory ownership

There are three deliberately separate layers:

1. **Private working memory** — owned by the executing agent. It is optional, run-scoped, bounded and disposable. It helps the agent remember local discoveries without distracting it with team bookkeeping.
2. **Shared task memory** — owned by the orchestration run. It contains only bounded handoffs needed by downstream agents. Old entries are evicted when budgets are reached.
3. **Durable learning ledger** — owned by the learning steward and dream cycle. It records reusable, evidence-backed lessons for current and future runs. Execution agents cannot promote their private notes directly into this store.

Repository files, tests and other authoritative evidence remain the source of truth. Memory is a cache of useful knowledge, not a replacement for verification.

## Execution versus dreaming

Learning analysis is deliberately outside the execution context:

```text
execute -> bounded handoffs -> finish
                         |
                         v
                    dream cycle
                         |
          +--------------+--------------+
          |                             |
     repeated success             repeated failure
          |                             |
     verified pattern             verified anti-pattern
          +--------------+--------------+
                         |
                         v
              bounded future guidance
```

The execution agent does not spend context on collective-memory curation. After the run, the dream cycle compares independent observations. Repeated successful approaches become reusable patterns; repeated failures or regressions become explicit anti-patterns. Conflicting evidence stays a candidate rather than poisoning future guidance.

Only distinct `run_id` values count as independent evidence. Retries inside one run cannot manufacture confidence. This makes the learning store improve through repeated experience rather than through repetition of the same mistake.

Verified failures are intentionally surfaced prominently in future guidance. An agent should see what previously wasted effort, caused regressions or failed acceptance criteria and avoid repeating it unless current evidence demonstrates that the condition has changed.

## Guardrails

- Every memory layer has hard size/count limits.
- Private memory is isolated by agent and run ID.
- Shared task memory is isolated by task intent digest.
- Concurrent writes use process-safe file locks and atomic replacement.
- Durable learning uses SQLite WAL transactions with a busy timeout for concurrent agents/processes.
- Durable records preserve the full observation and use immutable revisions linked by `learning_key` and `supersedes_id`.
- Schema and learning format are explicitly versioned; current versions are `3` and `3.0`.
- Duplicate observations are idempotent through a content fingerprint.
- Learning defaults to `candidate`; it is not treated as verified merely because an agent said it worked.
- Verified promotion requires independent observations with a consistent outcome.
- Rejected/superseded learning is not silently presented as current guidance.
- Long logs, complete transcripts, speculative reasoning and repeated tool output are never copied into durable learning.
- When context becomes too large, low-value/old working entries are evicted instead of failing the task.
- A single oversized memory item is rejected rather than breaking the memory budget.
- No memory operation requires a specific model provider or a second LLM summarizer.

## Learning steward and dream cycle

The execution agent should spend its effort on the task. The `learning-steward` is a separate, non-critical role that receives completed team evidence and performs the peripheral learning work. It is allowed to run even when another agent failed or was blocked, because failed paths are often the most valuable learning.

The steward records explicit outcomes such as `worked`, `failed`, `partial` and `regressed`, together with approach and evidence. The post-run `DreamMemory` curator then evaluates independent runs. Its output is a new verified revision, while the observations it supersedes remain available through the revision history.

The public API is intentionally small:

- `record(...)` — append one observation or revision.
- `revise(...)` — create a new immutable revision without destroying the prior detail.
- `relevant(...)` — retrieve bounded task-relevant knowledge, with failures boosted.
- `guidance(...)` — produce bounded execution guidance.
- `history(...)` — inspect every preserved revision for a learning key.
- `DreamMemory.dream(task)` — perform offline curation after execution.

This separation lets the durable store become richer over time without making execution prompts grow with it.

## Collective improvement loop

The intended property is compounding improvement:

```text
Run N
  -> execute from current verified knowledge
  -> capture evidence
  -> steward records observations
  -> dream compares independent outcomes
  -> verified patterns + anti-patterns improve the store

Run N+1
  -> retrieve only task-relevant verified knowledge
  -> avoid known bad paths
  -> start from proven approaches
  -> execute faster and more accurately
  -> capture new evidence
  -> dream again
```

The memory system therefore behaves more like collective institutional learning: experience is retained, bad patterns are explicitly remembered, good patterns gain confidence, conflicting evidence remains visible, and every new session can contribute another verified observation without carrying old transcripts into the working context.

## Handoff contract

A handoff contains only:

- objective
- completed status
- decisions
- evidence
- unresolved risks
- relevant files
- next action

The receiver gets direct dependency handoffs plus planner context rather than the complete run transcript.

## Model neutrality

The orchestration layer does not depend on a model-specific context format, tokenizer, prompt syntax or summarizer. A stronger model can improve the quality of the agent result without changing the orchestration contract. A weaker or local model receives the same bounded structured context.
