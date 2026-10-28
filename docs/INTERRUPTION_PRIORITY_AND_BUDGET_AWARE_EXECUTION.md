# Durable interruption priority and budget-aware execution

## Purpose

AUREN must accept new requests without losing the work already in progress. An interruption is a distinct task with its own durable identity, priority, parent link, state, and decision gate. It must not overwrite the task it interrupted.

## Implemented in this slice

- `ProgressJournal.record_interrupt(...)` appends an interruption to the existing append-only progress journal; it does not create a second task database.
- Priorities are integers from 0 to 100. The stack orders by descending priority, then timestamp and task ID for deterministic tie-breaking.
- `priority_stack()` includes all non-terminal tasks, including blocked and stale work, so operators can see outstanding asks rather than silently losing them.
- `next_ready()` chooses queued or recovering work, and never dispatches an item marked `decision_required`.
- Priority, parent link, and confirmation state persist across later progress events and process restart. Older journal rows without these fields remain readable with safe defaults.
- A blocked trade-off remains visible while unrelated ready work can continue.

## Scheduling contract for the canonical supervisor

The stack is a durable prioritization projection, not permission to bypass the canonical supervisor. Runtime integration must:

1. Persist each incoming request and its parent/interrupt relationship before acknowledging it.
2. Keep the current task checkpoint, remaining acceptance criteria, next safe action, deadline, budget, approvals, and evidence references intact when switching tasks.
3. Recompute priority only at safe checkpoints. Never kill a process or replay a side effect merely because a higher-priority request arrived.
4. Enforce aging or a fair-share policy so a continuous stream of urgent requests cannot starve older tasks. Any pre-emption must preserve resumable state.
5. Require explicit authorized confirmation for irreversible actions, scope changes, incompatible requirements, destructive cleanup, or material cost/quality/safety trade-offs. Mark only the dependent task blocked; continue independent, authorized work.
6. On restart, reconstruct the outstanding stack from the canonical journal and recovery ledger. If stores disagree or a task's outcome is ambiguous, mark it blocked/recovering and reconcile; do not infer success or replay an uncertain side effect.
7. Bound each work slice by deadline, context/token/output limits, tool calls, retries, and shared cost budget. Checkpoint verified progress between slices.
8. Use stable-boundary chunking, bounded streaming, batch processing, deduplication, caching, and resumable cursors where semantics permit. Sampling is permitted only for exploratory estimates; never sample security checks, policy enforcement, acceptance criteria, holdouts, or independent verification.
9. When no safe workaround fits the budget, emit a durable blocker with the exact limit, completed work, remaining work, safe alternatives, and the decision needed. Never truncate protocol frames or weaken Guard to force completion.
10. During otherwise idle capacity, run bounded introspection: inspect failures and near-limit events, create a candidate improvement with a measured hypothesis, test it against a pinned baseline/regression suite, and preserve negative results. Do not self-promote a candidate or change production policy without the required evidence and approval.

## Limits and next gates

This change adds durable journal-level priority ordering and human-decision gating. It does **not** by itself wire every supervisor/StateGraph/crew dispatch path to consume the stack, prove process-level pre-emption, implement a 24/7 background service, or establish fairness under sustained load. Those require canonical dispatch integration, restart/concurrency/starvation tests, and operator-managed runtime deployment. External evaluator custody, production isolation, and staging rollback remain independent evidence gates.

## Rollback

Revert the journal API and its tests together. Existing records remain append-only and are not deleted. If integration is enabled later, disable priority-based dispatch behind a default-off feature gate and continue to enforce the existing authorization and recovery rules.
