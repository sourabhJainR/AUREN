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

The current slice supplies typed plan/run data, strict schema-versioned serialization, and transactional SQLite persistence with compare-and-swap revisions. The store rejects incompatible schema/digests and stale writers; tests cover restart/resume, corruption, duplicate runs, and skipped revisions. This store persists planning state only: it does not replace the canonical runtime execution ledger or `RuntimeExecutionGuard` checkpoints, and it does not claim that a persisted plan has actually executed.

GSD Core's own guidance describes fresh-context specialist agents, thin orchestrators, human-readable project state and a Discuss → Plan → Execute → Verify → Ship loop. AUREN adapts those concepts while retaining its shared-budget, evidence, authority and rollback controls.


## Durable storage contract

`EngineeringPlanStore` stores canonical plan and run JSON, a schema version, the plan digest, run-state digest, and update timestamp in SQLite. Writes use `BEGIN IMMEDIATE`; `save_run` uses an expected current revision and requires the replacement to advance exactly one revision. A stale concurrent writer receives `PlanRevisionConflict`. On load, schema, plan digest, run-state digest, task set, plan identity, and source revision are checked before resume; tampered run progress fails closed. The store is intentionally separate from runtime execution budget checkpoints: the orchestration layer must persist both records consistently when it integrates the plan with a live execution.


## Recovery-ledger integration

`EngineeringPlanRecoveryBridge` binds the full plan/run record in `EngineeringPlanStore` to a digest-and-revision pointer in the canonical `EngineeringRecoveryLedger`. The store uses compare-and-swap revisions; the recovery ledger remains the canonical runtime recovery/checkpoint channel. The bridge verifies both records before a run can resume.

The plan store and recovery ledger use separate SQLite transactions, so a process crash can occur between their writes. The bridge deliberately fails closed if their digests or revisions disagree. It does not silently select a winner or recreate a missing checkpoint. An operator can reconcile only by supplying the expected plan digest and a non-empty reason; reconciliation republishes the current stored digest and does not alter task progress. This is an explicit recovery action, not proof that the underlying task was executed.

When every task reaches `passed` with evidence references, the bridge writes a verified checkpoint bound to the plan's source revision and the union of task evidence references. This verifies the plan lifecycle state only; it does not substitute for exact-head repository CI, independent evaluation, or production deployment evidence.
