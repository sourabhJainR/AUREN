# Context Efficiency and Quality Policy

The orchestrator optimizes for **verified outcome per model token**, not minimum tokens at any cost.

## 1. Keep always-loaded instructions small

Always-loaded agent instructions should contain only rules that are hard to infer from the repository and must be obeyed on every task. Do not repeat architecture, tool documentation, examples, or detailed workflows that the agent can discover when needed.

Prefer:

- repository-specific commands that are otherwise non-obvious
- non-obvious invariants
- required verification commands
- safety boundaries
- important naming/placement rules

Avoid generated inventories, long tutorials, duplicated tool documentation, and generic advice.

## 2. Retrieve, do not replay

Never inject the whole repository, whole memory store, whole graph, or full conversation when a targeted query can provide the required evidence.

Retrieval order:

`task contract -> local rules -> symbols -> graph paths -> exact search -> semantic search -> targeted source -> verification evidence`

Every retrieved item should have a reason for inclusion and provenance where available.

## 3. Budget context by task phase

Use separate budgets for:

- bootstrap/context discovery
- planning
- implementation
- verification
- review
- handoff

Do not let a large discovery phase consume the entire implementation context.

When a phase becomes context-heavy, summarize durable state into a compact handoff and start the next phase with fresh context.

## 4. Use small executable units

For complex work, decompose into independently verifiable slices. Prefer a sequence of small tasks with explicit acceptance criteria over one giant autonomous task. A slice should be small enough that its implementation and verification evidence remain easy to inspect.

Do not create artificial subtasks when the task is already simple.

## 5. Fresh-context verification

For meaningful changes, verification should be planned from the acceptance criteria rather than copied from the implementation. When practical, use a fresh reviewer/verification context that did not author the change.

The verifier must answer:

1. What behavior must be true?
2. What evidence would prove it?
3. What could make the current test pass while the feature is still wrong?
4. Are negative, boundary, compatibility, and failure paths covered?

## 6. Repository entropy check

After substantial work, inspect the change surface for stale documentation, contradictory comments, dead code, obsolete tests, temporary artifacts, merge-conflict remnants, and inconsistent behavior descriptions.

Do not turn cleanup into an unrelated refactor. Fix only contradictions or artifacts caused by, or materially blocking, the current task.

## 7. Cache stable evidence when the provider supports it

Reuse immutable or slow-changing evidence such as repository profile, symbol index, dependency graph, and stable instructions. Do not repeatedly retrieve identical content merely because a new model call started.

Cache invalidation must be tied to repository state, relevant file hashes, branch/commit, and provider version where available.

## 8. Route models and tools by difficulty

Use the least expensive capable model/provider for deterministic discovery, extraction, formatting, and simple checks. Reserve stronger reasoning models for ambiguous planning, architecture, difficult debugging, and final judgment.

Do not spawn a sub-agent when the parent can complete the task with less total context and fewer calls.

## 9. Optimize for total cost, not input tokens alone

Track, when available:

- input tokens
- cached input tokens
- output tokens
- tool calls
- wall-clock time
- retries
- context compactions
- verification failures
- final outcome

A shorter prompt that causes three retries is worse than a slightly larger prompt that succeeds once.

## 10. Learn from measured outcomes

Use evals and telemetry to identify which retrieval, routing, provider, and verification strategies improve task success. Do not promote a change because it merely reduced token count.

Primary objective:

`quality first -> reliability -> token efficiency -> latency -> cost`

A token-saving optimization that reduces verified task success must be rejected.
## 11. Evidence-driven retrieval recovery

A retrieval failure is a state transition, not permission to repeat the same fetch. The context pipeline records the failed retrieval mode in the existing task-memory ledger, preserves the failure as negative evidence, and selects a different bounded retrieval mode. Previously successful retrieval modes may be preferred, but current failures always take precedence.

Recovery is bounded:

`attempt -> observe -> classify -> pivot -> verify`

If every permitted retrieval mode fails, the pipeline stops and carries the failure and unknowns in the evidence envelope. It does not broaden scope, silently change permissions, or loop indefinitely.

This is the transferable Colibri principle: cache and reuse useful locality, use measured history to guide the next access, instrument misses, and fall back to a valid alternate path. HWS applies that principle to repository/context retrieval rather than copying Colibri's model-serving implementation.


## Typed decision advisor seam

The context pipeline accepts an optional typed decision_advisor callback for retrieval-mode selection. This is intentionally provider-neutral: a JEV adapter can supply a bounded Choice decision over the retrieval modes plus confidence, while the pipeline enforces the allowlist, failed-mode exclusion, confidence threshold, evidence collection, and terminal stop policy.

JEV is therefore a decision input, not an execution authority. If the advisor is unavailable, malformed, below threshold, or recommends a failed/unavailable mode, HWS falls back to the deterministic retrieval planner. No JEV SDK or network dependency is required by the core runtime.

The intended integration shape is:

bounded context state -> typed decision -> deterministic policy -> retrieval -> evidence -> verification

Keep questions atomic and let ordinary HWS code own policy and action. Model judgment may guide a decision, but repository instructions, security controls, evidence requirements, and execution authority remain deterministic.


## Adaptive allocation

Context acquisition now adapts its budget before each retrieval attempt. The
allocation uses uncertainty, risk, recent retrieval failure rate, active broker
pressure, and known working/failed retrieval modes. The allocation reserve is
excluded from the final context budget so fresh evidence and recovery retain
capacity instead of being crowded out by the initial selection.

This intentionally does not mean "use more context when uncertain". High
uncertainty can receive more evidence budget, while repeated failures or high
context pressure narrow the acquisition and preserve a reserve for fresh
evidence and recovery.

The resulting control loop is:

```text
task signals
  -> bounded allocation
  -> targeted retrieval
  -> failure?
       yes -> shrink/reframe + pivot
       no  -> score/deduplicate
  -> reserve + pack
  -> release
  -> outcome becomes future evidence
```

No model call is required for allocation, and the allocator cannot override
security, authorization, retrieval recovery, or verification policy.
