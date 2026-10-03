# Self-Improving Control Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen AUREN's existing context, decision, verification, graph, and learning spine into a deterministic self-improving control loop without introducing competing authorities.

**Architecture:** Extend `CodebaseIndex`, `TaskPlan`, the canonical engineering-state decision records, `StateGraph`, verification, and `agency_adaptive_planning`. Context stays derived from repository truth; decisions stay inside the canonical engineering-state ledger; learning remains advisory.

**Tech Stack:** Python 3, JSON Schema, pytest, existing AUREN portable runtime and GitHub Actions harness.

**Spec:** `docs/superpowers/specs/2026-09-16-self-improving-control-loop-design.md`

## Global Constraints

- Preserve one canonical owner per concern.
- Do not add a second repository graph, evidence store, workflow engine, memory store, planner, or capability catalog.
- Keep portable code provider-neutral and dependency-light.
- Learning cannot authorize actions, bypass verification, or weaken release gates.
- Probabilistic model output is never equivalent to verified fact.
- Preserve backward compatibility for existing public APIs unless a test proves a bug in their current contract.
- Use deterministic serialization and deterministic ordering for persisted/runtime decisions.

---

### Task 1: Context-control metadata and reuse

**Files:**
- Modify: `portable/agency_codebase_context.py`
- Test: `tests/test_codebase_context.py`

**Interfaces:**
- Produces `ContextPolicy`, `ContextReuseKey`, and enriched `CodebaseContext` fields while preserving existing `retrieve()` return compatibility.

- [ ] **Step 1: Write failing tests** for snapshot-bound context reuse, freshness, omitted-item accounting, and deterministic reuse keys.
- [ ] **Step 2: Run the focused context tests** and confirm the new expectations fail.
- [ ] **Step 3: Implement deterministic metadata and reuse helpers** without creating another index or cache authority.
- [ ] **Step 4: Run focused context tests** and confirm pass.
- [ ] **Step 5: Run existing repository-intelligence/context tests** to catch compatibility regressions.
- [ ] **Step 6: Commit** `feat: strengthen deterministic context control`

### Task 2: Richer TaskPlan execution intent

**Files:**
- Modify: `portable/task_planner.py`
- Test: `tests/test_task_planner.py`

**Interfaces:**
- Extend `Task` metadata with explicit parallel group, checkpoint, and verification strategy fields using backward-compatible defaults.

- [ ] **Step 1: Add failing tests** for stable serialization and dependency-safe parallel groups.
- [ ] **Step 2: Run focused planner tests** and confirm failure.
- [ ] **Step 3: Implement minimal fields and validation** in the existing planner.
- [ ] **Step 4: Run planner tests** and confirm pass.
- [ ] **Step 5: Run architecture contract tests** for owner invariants.
- [ ] **Step 6: Commit** `feat: make task plans execution-aware`

### Task 3: Typed probabilistic decision records

**Files:**
- Modify: `state/engineering-state.schema.json`
- Modify: `portable/evidence_contract.py`
- Test: `tests/test_evidence_contract.py`
- Test: `tests/test_architecture_contract.py`

**Interfaces:**
- Extend existing `$defs.decision` only; do not introduce another decision store.
- Validation must enforce probability range `[0,1]`, explicit abstention semantics, evidence references, and model metadata when probabilities are present.

- [ ] **Step 1: Add failing schema/contract tests** for typed values, probability bounds, abstention, and evidence references.
- [ ] **Step 2: Run focused tests** and confirm failure.
- [ ] **Step 3: Extend the canonical decision schema and validator** with backward-compatible optional fields.
- [ ] **Step 4: Run focused evidence and architecture tests** and confirm pass.
- [ ] **Step 5: Commit** `feat: add typed probabilistic decision evidence`

### Task 4: Graph joins, cancellation, and idempotency metadata

**Files:**
- Modify: `portable/agency_state_graph.py`
- Test: `tests/test_state_graph_hardening.py`

**Interfaces:**
- Preserve `StateGraph` and `CompiledStateGraph` as the only execution engine.
- Add explicit join behavior and cancellation tokens as runtime metadata, with deterministic result ordering.

- [ ] **Step 1: Add failing tests** for join barriers, cancellation propagation, and idempotency-key enforcement for retried idempotent nodes.
- [ ] **Step 2: Run focused graph tests** and confirm failure.
- [ ] **Step 3: Implement minimal join/cancellation/idempotency support** with existing effect declarations.
- [ ] **Step 4: Run all state-graph hardening tests** and confirm pass.
- [ ] **Step 5: Run graph runtime CI-equivalent tests.**
- [ ] **Step 6: Commit** `feat: strengthen graph control semantics`

### Task 5: Workflow evaluation and calibration

**Files:**
- Create: `portable/workflow_evaluation.py`
- Test: `tests/test_workflow_evaluation.py`
- Modify: `portable/evidence_contract.py`

**Interfaces:**
- `DecisionObservation` captures expected/reference probability, predicted probability, observed result, abstention, latency, cost, verification status.
- `WorkflowEvaluation` exposes deterministic `accuracy()`, `brier_score()`, `calibration_error()`, `abstention_rate()`, and `summary()`.
- Evaluation produces evidence/metrics; it cannot alter verification status or release gates.

- [ ] **Step 1: Write failing unit tests** for deterministic metrics and invalid observations.
- [ ] **Step 2: Run the focused evaluator tests** and confirm failure.
- [ ] **Step 3: Implement the evaluator** with no model/provider dependencies.
- [ ] **Step 4: Run focused tests** and confirm pass.
- [ ] **Step 5: Wire evaluator summaries into existing evidence validation** without changing canonical ownership.
- [ ] **Step 6: Commit** `feat: add provider-neutral workflow evaluation`

### Task 6: Compound engineering lessons

**Files:**
- Modify: `portable/agency_adaptive_planning.py`
- Test: `tests/test_adaptive_planning.py`

**Interfaces:**
- Add immutable `EngineeringLesson` and `LessonOutcome` records to the existing learning module.
- Lessons reference evidence IDs and task classes and include recurrence observations.

- [ ] **Step 1: Add failing tests** for lesson validation, recurrence comparison, and advisory-only recommendation behavior.
- [ ] **Step 2: Run focused adaptive-planning tests** and confirm failure.
- [ ] **Step 3: Implement lessons and recommendation hooks** without granting authority.
- [ ] **Step 4: Run adaptive-planning tests** and confirm pass.
- [ ] **Step 5: Add regression tests proving learning cannot alter verification/release state.**
- [ ] **Step 6: Commit** `feat: compound verified engineering lessons`

### Task 7: Autonomous lifecycle control loop

**Files:**
- Modify: `portable/execution_contract.py`
- Modify: `state/engineering-state.schema.json`
- Test: `tests/test_execution_contract.py`
- Test: `tests/test_state_graph_hardening.py`

**Interfaces:**
- Add lifecycle receipt fields for wake reason, iteration, prior outcome, and next action references without turning the envelope into a durable state store.

- [ ] **Step 1: Add failing tests** for repeatable lifecycle identity and guarded wake/continue behavior.
- [ ] **Step 2: Run focused contract tests** and confirm failure.
- [ ] **Step 3: Implement lifecycle receipt support** with explicit terminal/blocked states.
- [ ] **Step 4: Run focused tests** and confirm pass.
- [ ] **Step 5: Verify release gates still require verification evidence.**
- [ ] **Step 6: Commit** `feat: close the autonomous engineering lifecycle loop`

### Task 8: Architecture contract, docs, and end-to-end regression

**Files:**
- Modify: `architecture/architecture.yaml`
- Modify: `docs/ARCHITECTURE_CURRENT.md`
- Modify: `docs/FINAL_ARCHITECTURE_REVIEW.md`
- Modify: `tests/test_architecture_contract.py`
- Modify: `.github/workflows/architecture-integrity.yml` only if enforcement needs a new hard-fail assertion

**Interfaces:**
- Architecture contract remains machine-readable authority.

- [ ] **Step 1: Add failing contract tests** for the new invariants and canonical ownership.
- [ ] **Step 2: Implement contract/documentation updates.**
- [ ] **Step 3: Run the complete deterministic system test suite.**
- [ ] **Step 4: Run the full CI-equivalent test/harness suite and inspect every workflow, not only summary status.**
- [ ] **Step 5: Fix any regression and repeat the full suite until every check is green.**
- [ ] **Step 6: Commit** `docs: codify the self-improving control loop`

### Task 9: Final verification and integration

**Files:**
- No new architecture files unless a failing check demonstrates a missing contract.

- [ ] **Step 1: Inspect the complete diff for duplicate authorities, accidental compatibility promotion, and provider coupling.**
- [ ] **Step 2: Run every available GitHub Actions check for the branch head and inspect failed job logs if any.**
- [ ] **Step 3: Confirm portable bundle size/version/integrity and all architecture/harness checks.**
- [ ] **Step 4: Open the PR against `main`.**
- [ ] **Step 5: Iterate on all CI failures until every required check is green.**
- [ ] **Step 6: Merge only after the green matrix is confirmed.**
