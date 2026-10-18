# Unified Graph and Crew Runtime Contract

**Status:** architecture alignment and integration contract; not a claim that every execution surface has already converged.
**Review baseline:** `2460cf55ec16f7de2b0bad306c85dd269b732d7b` (main, 2026-10-13).

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
| Resource-aware harness team strategy | `.ai-harness/runtime/graph_agent_team.py` | Builds its agent graph on `portable.agency_state_graph.StateGraph` and owns resource/learning strategy behavior. It consumes the shared crew-plan and proposal-validation helpers, uses a checkpointable task-scoped retry ledger for read-only agent retries, and registers trusted host adapters through `runtime.register_crew_adapters`. The portable handoff controller supports idempotent handoff IDs, checkpoint snapshots, retry-budget charging, and a host-injected persistence writer. End-to-end convergence is still incomplete until the real supervisor owns that writer and shares one ledger across graph, handoff, and supervisor recovery. |
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

## Shared execution budget contract

`AdaptiveRuntime.run()` owns one live `RuntimeExecutionGuard` for the complete supervised task. The same object is passed into each supervisor iteration, then reused by `Orchestrator` graph nodes and `GraphAgentTeam` rather than creating a fresh per-layer budget. The guard snapshot is stored with the supervisor recovery checkpoint and resumed only from that trusted ledger; caller-supplied live guard objects or snapshots are ignored.

Graph nodes and specialist adapters that need model inference should call `HybridModelRouter.route_with_context(request, state)` (or explicitly pass `state["_auren_runtime_execution_guard"]` to `route(..., execution_guard=...)`). Specialist proposal dispatches are charged to the same guard with stable idempotency keys, and a previously charged proposal is not re-executed under the same scope. Model discovery is blocked before probing when the shared attempt budget is already exhausted. The route-local attempt/deadline limits remain in force and cannot enlarge the shared task budget. Python callbacks that ignore cancellation remain non-preemptible; hard termination requires a process-isolated adapter.

Regression coverage verifies guard identity across supervisor retries, checkpoint/resume of charged attempts, specialist-graph guard reuse, and that a second model route cannot execute or even discover models after the shared attempt budget is consumed.

## Trusted adapter bootstrap

The runtime does not invent provider-backed crew adapters. A trusted host creates each `ApprovedToolCrewAdapter` with an explicit read-only `ApprovedToolRegistry`, then registers the configured mapping before task execution:

```python
from runtime import register_crew_adapters

register_crew_adapters({
    "frontend_ui": frontend_adapter,
    "backend_api": backend_adapter,
    "qa_reviewer": qa_adapter,
    "security_privacy": security_adapter,
})
```

The example names illustrate the registration shape; the variables must be real, reviewed adapter instances. Registration rejects unknown crew IDs, mismatched adapter IDs, missing `propose(request)`, missing stable configuration digests, and adapters without an explicit `implementation_version`. The version is included in the adapter configuration digest so planner/finalizer implementation changes invalidate stale crew plans and checkpoints. A per-task option cannot disable a constructor-level crew requirement. High/critical or sensitive mutation/review phases fail closed for missing selected adapters; read-only planning can continue. The crew node is marked external, and graph node execution is bounded by the configured resource timeout.

## Supervisor evidence binding

When the active project root is a Git repository and a repository slug is configured (or resolved from its origin), the supervisor captures that repository and HEAD once for a new task and preserves the identity across resume. Each durable handoff checkpoint is digest-checked in supervisor SQLite and indexed in the canonical evidence ledger against that exact revision. Legacy checkpoints without revision identity remain explicitly unbound on resume; AUREN must not retroactively attribute them to the current HEAD.

The local ledger row uses the explicit signer label `local-supervisor-unattested`: it proves local checkpoint integrity and revision linkage, **not** a cryptographic signature, an independently operated QA attestation, or external custody. Independent reviewer receipts remain bound into the handoff checkpoint; promotion still requires the separately configured Guard/decision-record verification and real operator-run acceptance.

## Required invariants

1. A single immutable intent digest follows every graph node, crew proposal, handoff, retry, checkpoint, evidence item, and final report.
2. The crew plan digest binds the graph digest, intent digest, selected crew contracts, and adapter configuration. Each proposal receipt includes its own digest, adapter implementation version, adapter configuration digest, intent digest, graph digest, and crew-plan digest. Missing or changed registrations invalidate the relevant receipt.
3. Crew selection is deterministic and bounded. Only selected, registered crews can be dispatched; tool allow-lists are per crew and read-only during proposal.
4. Crew output is a proposal. A successful proposal or its self-reported acceptance checks cannot satisfy independent verification, reviewer independence, exact-head CI, decision records, or Guard authorization. The harness run artifact carries the plan and proposal receipts; these are evidence references, not a parallel task state store.
5. Read-only independent nodes may run in bounded parallel waves. Mutations with shared resources are serialized behind dependencies. Failed dependencies block only dependent work.
6. Checkpoint/resume preserves graph state, task identity, crew-plan digest, attempts, evidence references, and last-known-good revision. When crew dispatch is enabled, the execution-graph digest is checked against the saved checkpoint before resume; a changed crew/adapter configuration fails closed. PR #661 exercises the real `ExecutionController.execute_graph()` → `GraphAgentTeam` path, process interruption after enqueue/claim, supervisor recreation and SQLite resume, idempotent replay, independent review, and aligned supervisor/handoff retry-budget digests.
7. The harness graph carries a checkpointable, task-scoped retry ledger across parallel read-only agent retries. The supervisor-owned handoff path persists queue/claim/retry/completion state and rejects stale checkpoints that would refund retry charges or revive exhausted work. This closes the repository-level handoff/recovery scope tracked by issue #438; it does not merge the two graph kernels into one implementation.
8. Durable memory and evidence have one implementation/owner; working memory and projections are scoped views, not competing sources of truth.
9. Runtime configuration and code must agree with this document. A documented future adapter is not counted as an implemented adapter.
10. Any compatibility surface must have a canonical owner, replacement, and removal condition in `architecture/compatibility.yaml`.

## Known integration gap on the review baseline

The portable crew facade (`portable.state_graph.GraphAgentTeam`) and resource-aware harness team (`.ai-harness/runtime/graph_agent_team.py`) retain distinct graph kernels, but the harness now builds the same canonical crew plan and dispatches registered adapters from a graph-owned `crew-proposals` node. When no adapters are registered, the run records the missing assignments; for high/critical mutation and review phases, missing adapters block mutation while safe read-only planning may continue. The trusted host registers adapters at bootstrap with `runtime.register_crew_adapters(...)`; task/model output cannot register or broaden them.

**Status refresh — 2026-10-16:** issue [#438](https://github.com/sourabhJainR/AUREN/issues/438) was closed after PRs [#658](https://github.com/sourabhJainR/AUREN/pull/658) and [#661](https://github.com/sourabhJainR/AUREN/pull/661) merged. Those changes provide repository-level supervisor-owned handoff persistence, restart/resume, idempotent replay, independent-review gating, and retry-budget alignment. The graph kernels remain distinct by design and must not be described as fully unified. Host adapters are explicitly registered; provider-specific live adapters are not configured by default. Operator-run migration/rollback acceptance remains tracked separately in [#534](https://github.com/sourabhJainR/AUREN/issues/534), and the first independent external campaign remains tracked in [#404](https://github.com/sourabhJainR/AUREN/issues/404).

## Verification commands

```bash
python scripts/validate_architecture.py
python -m unittest tests.test_architecture_contract tests.test_contract_alignment tests.test_crew_adapters -v
python -m unittest discover -s tests -p 'test_*.py'
```

The last command is the broad local suite; repository CI remains the authoritative result for the exact PR head. The current implementation exposes a host-injected adapter registry but does not ship provider-specific live adapters by default. Post-merge checks and any operator-controlled migration or external Arena campaign are separate evidence gates.
