# Unified Graph and Crew Runtime Contract

**Status:** architecture alignment and integration contract; not a claim that every execution surface has already converged.
**Review baseline:** `492799ee7237ba690a3ce23db2012997855d1cb3` (main, 2026-10-13).

## Goal

AUREN is one governed engineering system. It may retain specialized graph implementations behind explicit adapters, but it must not have competing owners for task intent, dependencies, authority, evidence, crew dispatch, verification, or promotion.

## Canonical ownership today

| Concern | Current owner / surface | Contract |
|---|---|---|
| Task intent and dependency readiness | `portable.task_planner.TaskPlan` | One dependency contract; reject cycles and missing prerequisites. |
| Default adaptive execution | `portable.adaptive_runtime.AdaptiveRuntime` with `portable.orchestration.Graph/Orchestrator` | Owns the default bounded task run, guard receipt, evidence trajectory, and supervisor recovery handoff. |
| Durable task lifetime | `portable.execution_controller.ExecutionController` + `portable.autonomous_company.AutonomousCompany` | Owns persisted work units, handlers, resume, CI/promotion gate policy, and terminal status. |
| Checkpointable graph kernel used by the harness team | `portable.agency_state_graph.StateGraph` | Owns JSON-safe graph state, checkpoints, reducers, interrupts, bounded supersteps, and effect-aware retries. It is not a second task planner or policy authority. |
| Supervisor-facing compatibility facade | `portable.state_graph.StateGraph` | Wraps `portable.orchestration.Graph/Orchestrator`; do not treat it as the checkpointable kernel. |
| Specialist contracts and scoped proposal tools | `portable.crew_adapters` plus `portable.state_graph.GraphAgentTeam` | Owns crew contract validation, selected-crew plan digest, scoped read-only tool proposals, and handoff validation. Proposals do not authorize changes. |
| Resource-aware harness team strategy | `.ai-harness/runtime/graph_agent_team.py` | Currently builds its own agent graph on `portable.agency_state_graph.StateGraph` and owns resource/learning strategy behavior. It must consume the canonical crew contracts through an explicit adapter before it can be described as fully integrated with the portable crew facade. |
| Capability selection | `portable.agent_capabilities.CapabilityFabric` | Owns capability semantics, risk and provider eligibility. Crew adapters cannot broaden it. |
| Durable memory | `portable.persistent_memory.PersistentMemory` (compatibility wrapper over `portable.agent_capabilities.PersistentMemory`) | One storage implementation; scoped wrappers may adapt APIs but must not create a second memory truth. |
| Verification and promotion | Existing verification, decision-record, Guard, Arena, CI and rollback gates | Neither graph nor crew can self-approve, promote, merge, waive Guard, or convert model confidence into verification. |

## End-to-end control flow

```text
User intent + constraints
        |
        v
Intent contract + repository snapshot + design guard
        |
        v
TaskPlan (dependencies, acceptance, risk, changed scope)
        |
        v
AdaptiveRuntime / durable supervisor
        |
        +--> canonical graph execution adapter
        |       |
        |       +--> deterministic nodes
        |       +--> selected specialist crews (proposal-only)
        |       +--> bounded parallel read-only work
        |       +--> serialized mutations after dependencies
        |
        v
Evidence envelope + exact revision identity
        |
        v
Verification -> independent review -> regression -> CI / decision record
        |
        v
Guard-controlled canary / promotion / rollback
        |
        v
Outcome evidence -> advisory learning -> future task policy
```

## Required invariants

1. A single immutable intent digest follows every graph node, crew proposal, handoff, retry, checkpoint, evidence item, and final report.
2. The crew plan digest binds the graph digest, intent digest, crew contract, and adapter configuration. Missing or changed registrations invalidate the relevant receipt.
3. Crew selection is deterministic and bounded. Only selected, registered crews can be dispatched; tool allow-lists are per crew and read-only during proposal.
4. Crew output is a proposal. A successful proposal or its self-reported acceptance checks cannot satisfy independent verification, reviewer independence, exact-head CI, decision records, or Guard authorization.
5. Read-only independent nodes may run in bounded parallel waves. Mutations with shared resources are serialized behind dependencies. Failed dependencies block only dependent work.
6. Checkpoint/resume preserves graph state, task identity, crew-plan digest, handoff queue, attempts, evidence references, and last-known-good revision. Stale or mismatched state fails closed.
7. One recovery budget governs retries across graph nodes, crew handoffs, and supervisor recovery. No layer silently starts its own unbounded retry loop.
8. Durable memory and evidence have one implementation/owner; working memory and projections are scoped views, not competing sources of truth.
9. Runtime configuration and code must agree with this document. A documented future adapter is not counted as an implemented adapter.
10. Any compatibility surface must have a canonical owner, replacement, and removal condition in `architecture/compatibility.yaml`.

## Known integration gap on the review baseline

The portable crew facade (`portable.state_graph.GraphAgentTeam`) and the resource-aware harness team (`.ai-harness/runtime/graph_agent_team.py`) are distinct execution paths. The former uses `portable.orchestration.Graph/Orchestrator`; the latter builds on `portable.agency_state_graph.StateGraph`. The current repository contains scoped crew adapters and tests for the portable facade, but the reviewed harness team does not import or invoke those adapters. Therefore the repository must not claim that every default harness task automatically dispatches the typed crews.

Track this convergence under [issue #438](https://github.com/sourabhJainR/AUREN/issues/438). Completion requires real end-to-end tests on the default harness path, not just contract-unit tests.

## Verification commands

```bash
python scripts/validate_architecture.py
python -m unittest tests.test_architecture_contract tests.test_contract_alignment tests.test_crew_adapters -v
python -m unittest discover -s tests -p 'test_*.py'
```

The last command is the broad local suite; repository CI remains the authoritative result for the exact PR head. Post-merge checks and any operator-controlled migration or external Arena campaign are separate evidence gates.
