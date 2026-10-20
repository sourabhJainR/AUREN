# Graph-Driven Engineering Gate Crew Plan

The module `portable.engineering_gate_crew_plan` defines the remaining AUREN engineering gates as a deterministic dependency DAG and routes each gate through the canonical `portable.state_graph.build_crew_plan` crew planner.

## Operating contract

- The graph defines dependencies and readiness; specialist crews produce scoped proposals and evidence references.
- Crew assignments use canonical crew contracts. Missing production adapters are surfaced explicitly; they are never treated as completed work.
- A gate cannot start until all prerequisites are passed.
- Passing requires evidence references. Isolation, guarded promotion, operator migration/rollback, and independent Arena campaign gates additionally require a trusted external evidence verifier.
- The module is a planning/transition validator, not a second queue or persistence store. `AdaptiveRuntime`, `ExecutionController`, `EngineeringPlanRecoveryBridge`, and the canonical evidence ledger remain authoritative for durable execution, checkpoints, authorization, verification, and recovery.
- Do not wire a worker-authored boolean or an LLM response as the external evidence verifier. It must validate actual out-of-band operator/Arena evidence and trust roots.

## Gate DAG

1. `post_merge_evidence`: exact-main checks and portable artifacts.
2. `durable_plan_dispatch`: plan/run checkpoint binding and idempotent dispatch.
3. In parallel after gate 2: `runtime_isolation_reconciliation`, `provenance_learning`, `safe_parallel_execution`, and `unified_dashboard`.
4. `guarded_promotion`: depends on isolation, learning, and parallel execution.
5. `operator_migration_rollback`: requires isolation, promotion, and dashboard.
6. `independent_arena_campaign`: requires isolation, provenance learning, and guarded promotion.

The planner is intentionally conservative: a skipped check is not a pass; CI is not evidence of production isolation; model confidence is not independent evaluation; and a gate with missing evidence remains incomplete.

## API

- `gate_definitions()` returns the validated immutable gate DAG.
- `build_gate_crew_plan(graph_digest, intent_digest, agents)` creates per-gate crew plans and reports missing registered adapters.
- `ready_gates(statuses)` returns only gates whose dependencies have passed.
- `transition_gate(...)` validates a single transition and requires a trusted verifier for external-evidence gates.
- `gate_plan_digest(work_items)` creates a stable content digest for the crew plan.

This module does not claim that the external gates have been executed. Their completion requires linked, independently verifiable evidence.
