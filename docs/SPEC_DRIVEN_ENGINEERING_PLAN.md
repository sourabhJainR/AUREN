# Spec-driven engineering plans in AUREN

AUREN's `portable.engineering_plan` module adopts useful workflow concepts from GSD Core without adding GSD Core as a dependency. It is a versioned contract for planning and task lifecycle; it is not a second execution controller.

## Lifecycle mapping

1. **Discuss** — turn ambiguity into explicit requirements, assumptions, and recorded decisions before plan creation.
2. **Plan** — define task dependencies, risk, acceptance criteria, budgets, source revision, and a rollback plan for every task.
3. **Execute** — schedule only dependency-ready tasks. The canonical `AdaptiveRuntime` / `RuntimeExecutionGuard` remains responsible for shared attempts, steps, deadline and cancellation.
4. **Verify** — move tasks through `running → verifying → passed`. A task cannot pass without evidence references. A worker's self-reported success is not evidence.
5. **Replan** — failed or blocked work can return to `ready` only with a reason and incremented `replan_generation`. This makes targeted gap closure explicit and auditable.
6. **Ship** — repository workflow remains exact-head CI, review, merge, and post-merge main verification; this data model does not bypass those gates.

## Contract guarantees

- Plan and run schemas are explicitly versioned.
- Canonical JSON hashing gives deterministic plan/run digests.
- Missing dependencies, duplicate task IDs, dependency cycles, malformed budgets and empty acceptance criteria fail at plan construction.
- Resume checks the plan digest, task set and source revision before reuse; incompatible plans are rejected rather than silently resumed.
- Lifecycle transitions are constrained; passed and cancelled tasks are terminal. Passing requires evidence references.
- Reopening failed/blocked work increments a replan generation to prevent invisible retry loops.
- Each task includes a rollback plan; this module records the plan but does not claim that rollback was executed.

## Context and handoff contract

Keep worker context compact and task-scoped: goal/requirements, task and dependency IDs, relevant codebase conventions, exact source revision, applicable policies, acceptance criteria, prior verified evidence, unresolved questions, budget remaining, and the next safe action. Handoffs must preserve approvals, safety constraints, failure history and rollback instructions. Context compaction must not turn missing evidence into a pass.

## Current boundary

This first slice supplies typed plan/run data and lifecycle transitions. Durable storage, crash-safe state persistence, generated context packets, parallel workstream isolation, and dashboard projections remain follow-on work. Do not treat an in-memory `EngineeringPlanRun` as a durable checkpoint. Persist it through AUREN's canonical execution ledger in a subsequent integration step, preserving its digest and revision checks.

GSD Core's own guidance describes fresh-context specialist agents, thin orchestrators, human-readable project state and a Discuss → Plan → Execute → Verify → Ship loop. AUREN adapts those concepts while retaining its shared-budget, evidence, authority and rollback controls.
