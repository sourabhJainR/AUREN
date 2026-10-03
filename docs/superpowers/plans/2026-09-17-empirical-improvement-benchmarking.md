# Empirical Improvement and Benchmarking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the integrated AUREN runtime empirically improve output quality over time while keeping execution workers focused and moving auxiliary learning, memory consolidation, compaction, and benchmarking off the active task path.

**Architecture:** Keep `DreamMemory` as the post-run consolidation boundary. Add a durable workstyle/quality profile, a deferred maintenance queue, and an empirical improvement harness that compares baseline versus candidate strategy outcomes using replayable benchmark cases. The worker retrieves existing guidance and records compact outcomes; it does not perform consolidation or compaction. Promotion remains evidence- and regression-gated.

**Tech Stack:** Python 3, stdlib `dataclasses`/`sqlite3`/`json`/`hashlib`, existing `PersistentMemory`, `DreamMemory`, `DeepEvaluator`, `ContinualLearningGuard`, `AdaptiveRuntime`.

**Spec:** Approved chat design from 2026-09-17: worker lane stays task-focused; learning lane consolidates episodes, compacts memory, updates user/workstyle profile, evaluates quality and strategy candidates; verified learning is promoted only after regression/benchmark evidence.

## Global Constraints

- Dream Memory remains post-run and outside the execution graph.
- Auxiliary memory/learning/compaction work must not become worker execution authority.
- Learned guidance remains advisory until evidence-backed promotion gates pass.
- Existing public runtime APIs and safety boundaries remain compatible.
- All new production behavior is developed test-first and covered by deterministic tests.
- Benchmarking must be replayable and expose quality, retry/iteration, calibration, and regression metrics.

---

### Task 1: Deferred learning and workstyle profile

**Files:**
- Create: `portable/adaptive_learning.py`
- Modify: `portable/adaptive_runtime.py`
- Modify: `portable/__init__.py`
- Test: `tests/portable/test_adaptive_learning.py`

**Interfaces:**
- `WorkStyleProfile(project: str, preferred_detail: str, verification_emphasis: str, iteration_target: float, confidence: float, observations: int)`.
- `DeferredLearningJob(job_id: str, project: str, task_id: str, kind: str, payload: dict[str, object], status: str)`.
- `AdaptiveLearningStore(memory: PersistentMemory, project: str, dream_root: Path | str | None = None)` with `record_outcome(...)`, `pending(limit=...)`, `process(limit=...)`, `profile()`, and `guidance()`.
- `AdaptiveRuntime.run(..., learning_context=...)` records a compact deferred outcome instead of doing cognitive consolidation synchronously.
- `AdaptiveRuntime.process_learning(...)` drains deferred jobs outside the worker path and invokes cognitive learning plus Dream Memory.

- [x] **Step 1: Write failing tests** for durable workstyle observations, deferred-job creation, explicit draining, and worker-path isolation.
- [x] **Step 2: Run the full CI suite and use the failing legacy assertion to confirm the old synchronous contract is incompatible with the approved architecture.
- [x] **Step 3: Implement the SQLite-backed deferred store and explicit maintenance method; preserve `DreamMemory` as the consolidation engine.
- [x] **Step 4: Run focused tests and the existing cognitive/adaptive runtime tests; update the legacy assertion to verify deferred learning.
- [x] **Step 5: Verify all repository CI workflows on the current PR head.

### Task 2: Empirical quality and iteration benchmark harness

**Files:**
- Create: `portable/empirical_improvement.py`
- Modify: `portable/__init__.py`
- Test: `tests/portable/test_empirical_improvement.py`

**Interfaces:**
- `ImprovementObservation(case_id: str, strategy: str, score: float, iterations: int, confidence: float, evidence: tuple[str, ...])`.
- `ImprovementReport(baseline_score, candidate_score, score_delta, baseline_iterations, candidate_iterations, iteration_delta, calibration_delta, accepted, reason, digest)`.
- `EmpiricalImprovement.evaluate(baseline, candidate, min_score_gain=0.01, max_iteration_increase=0)`.
- `EmpiricalImprovement.run(cases, baseline_strategy, candidate_strategy, evaluator)` returns a report and rejects regressions, missing evidence, and iteration increases beyond the configured bound.
- Benchmark output must be deterministic for identical case inputs.

- [x] **Step 1: Write failing tests for measurable quality improvement, reduced/equal iterations, evidence requirements, regression rejection, and deterministic digesting.
- [x] **Step 2: Run focused/full CI and confirm the new behavior is compatible with the repository suite.
- [x] **Step 3: Implement the evaluator wrapper with deterministic observation metrics and fail-closed promotion gates.
- [x] **Step 4: Run focused tests plus the existing deep-evaluation and continual-learning suites.
- [x] **Step 5: Verify all repository CI workflows on the current PR head.

### Task 3: Integrated learning loop and benchmark scenario

**Files:**
- Modify: `portable/adaptive_runtime.py`
- Create: `tests/portable/test_integrated_learning_benchmark.py`
- Create: `examples/empirical_improvement_demo.py`

**Interfaces:**
- `AdaptiveRuntime.process_learning(project_root, limit=...) -> list[dict[str, object]]` performs bounded maintenance outside task execution.
- `AdaptiveRuntime.benchmark_improvement(project_root, cases, ...) -> ImprovementReport` runs replayable empirical comparison without mutating active execution authority.
- Benchmark scenario exercises prior result guidance, workstyle adaptation, deferred learning, quality score, iteration count, and confidence/regression gating.

- [x] **Step 1: Write the integration tests proving `run()` leaves maintenance pending and `process_learning()` performs consolidation/adaptation afterward.
- [x] **Step 2: Run CI and verify the integration tests coexist with the existing runtime contracts.
- [x] **Step 3: Add the bounded integration APIs and deterministic example scenario.
- [x] **Step 4: Run the full deterministic repository system suite.

### Task 4: PR review and CI gate

- [x] Open PR #126 from `feat/empirical-improvement-benchmarking` into `main`.
- [x] Investigate and fix the legacy synchronous-learning regression exposed by the merge-ref full suite.
- [x] Review the diff for correctness, state isolation, determinism, backwards compatibility, and preservation of Dream Memory boundaries.
- [ ] Fix any remaining review findings, then wait for every CI check on the latest PR head.
- [ ] Merge only when the complete check set is green.
- [ ] Verify the resulting `main` commit and final repository state.
