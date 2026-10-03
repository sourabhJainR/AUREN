# Self-Improving Control Loop Design

## Goal
Strengthen AUREN's existing engineering spine with explicit context control, typed probabilistic decisions, workflow evaluation, compound learning, and graph-level autonomy without introducing a second planner, graph, evidence store, memory authority, capability catalog, or workflow engine.

## Architecture
The canonical path remains:

`intent -> CodebaseIndex -> evidence -> TaskPlan -> ExecutionEnvelope -> StateGraph -> verification/review -> regression/release -> outcome -> learning`

New behavior is implemented as extensions of existing canonical authorities. Context remains a derived view of `CodebaseIndex`; decisions remain records in the canonical engineering-state ledger; workflow evaluation is verification support; compound learning remains advisory; StateGraph remains the sole execution engine.

## Design

### Context control
Every generated context package records repository snapshot identity, retrieval recipe, token budget, selected items, omitted items, freshness, and confidence. Context operations are deterministic and derived from `CodebaseIndex`: retrieve, rank, prune, compress, refresh, and reuse. No second repository index is introduced.

### Planning artifact
`TaskPlan` remains the sole planner and gains explicit support for intent, research, assumptions, dependencies, parallel groups, checkpoints, acceptance, and verification strategy without replacing the existing task model.

### Graph semantics
`StateGraph` remains canonical. Extensions cover explicit parallel groups, joins, cancellation propagation, idempotency metadata, deterministic terminal states, and graph invariants. Provider/model/tool execution stays outside the graph module.

### Probabilistic decisions
The existing `engineering-state.schema.json` decision record gains optional typed output, probability, calibration metadata, model/provider, method, abstention, and observed outcome fields. Model belief never upgrades verification state.

### Workflow evaluation
Verification gains provider-neutral workflow evaluation based on decision correctness, calibration, abstention quality, verification agreement, latency, cost, and rework. Evaluation consumes canonical evidence and verification records and cannot authorize release.

### Compound learning
Adaptive learning records reusable engineering lessons: problem, cause, solution, evidence, prevention rule, applicability, and recurrence outcome. Lessons are advisory recommendations only.

### Autonomous loop
The outer lifecycle can repeat discover -> select -> plan -> execute -> verify -> review -> compound -> sleep/wake. Discovery and wake conditions remain policy-gated and downstream release controls are unchanged.

## Safety invariants

- One canonical owner per concern remains mandatory.
- Evidence is snapshot-bound and stale evidence must be refreshed.
- Probabilities are never treated as facts.
- Learning cannot authorize, bypass verification, weaken release gates, or mutate canonical truth.
- Parallel work must have explicit joins and deterministic merge behavior.
- Portable code remains provider-neutral and dependency-light.
- Historical compatibility surfaces remain classified and are not promoted back into canonical architecture.

## Test strategy

Add unit tests for context metadata/reuse, decision validation and calibration, workflow evaluation, compound lessons, graph joins/cancellation, and architecture-contract uniqueness. Extend existing end-to-end harness coverage so the new constructs are exercised without creating a second runtime path.
