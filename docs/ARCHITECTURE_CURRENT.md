# Current Architecture

This document describes the current AUREN 23.x architecture. Historical version notes remain provenance records and do not redefine runtime ownership.

## Engineering spine

```text
User task
  -> intent / contract
  -> CodebaseIndex repository facts
  -> deterministic context control
  -> canonical evidence ledger
  -> typed/probabilistic decisions + Decision Fabric (trust / uncertainty / resources)
  -> TaskPlan
  -> ExecutionEnvelope
  -> StateGraph
  -> verification / workflow evaluation / review
  -> regression / release
  -> outcome
  -> compound learning
  -> guarded wake / next iteration
```

## Canonical authorities

| Concern | Authority |
| --- | --- |
| Repository truth | `portable.agency_codebase_context.CodebaseIndex` |
| Context derivation | `portable.agency_codebase_context.CodebaseContext` |
| Evidence truth | `state/engineering-state.schema.json:evidence` |
| Decisions | `state/engineering-state.schema.json:decisions` + `portable.decision_fabric.DecisionFabric` |
| Planning | `portable.task_planner.TaskPlan` |
| Cross-phase contract | `portable.execution_contract.ExecutionEnvelope` |
| Execution | `portable.agency_state_graph.StateGraph` |
| Capabilities | `portable.agent_capabilities.CapabilityFabric` |
| Verification and workflow evaluation | `state/engineering-state.schema.json:verification` plus `portable.workflow_evaluation.WorkflowEvaluation` |
| Release gates | `docs/REGRESSION_CANARY.md` |
| Learning / compound lessons | `portable.agency_adaptive_planning` |
| Architecture views | `skills/engineering/interactive-documentation` |

## Operating rules

No second repository graph, evidence store, workflow engine, capability catalog, or memory authority is introduced. Context is a snapshot-bound derived view. Probabilities describe model belief and never become verification truth. Workflow evaluation is measurement only. Learning can recommend strategy and prevention rules, but cannot authorize actions, weaken gates, or replace canonical state.

Parallel work remains an execution strategy on the canonical `StateGraph` and must converge through deterministic merge and verification. Evidence references remain snapshot-bound and stale evidence must be refreshed. The portable layer remains provider-neutral and dependency-light.

## Cross-cutting Decision Fabric

The typed DecisionFabric is the policy bridge between observed evidence and execution choices. Evidence trust, uncertainty, model disagreement, and resource constraints influence verification depth and execution lane without replacing canonical evidence, provider, capability, planner, graph, or scheduler ownership.
