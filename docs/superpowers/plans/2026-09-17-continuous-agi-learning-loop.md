# Continuous AGI-Aligned Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuously turn each completed task into durable experience and use long-running evidence to improve future strategy selection, confidence calibration, and iteration efficiency without mutating an active task.

**Architecture:** The active runtime snapshots the current immutable adaptive policy, executes normally, and appends outcome evidence. A durable `AutomationScheduler` owns the maintenance lane; maintenance drains deferred learning, evaluates persistent experience history, and creates bounded versioned policies plus auditable receipts. Existing safety, verification, cognitive learning, and rollback boundaries remain authoritative.

**Tech Stack:** Python, SQLite, existing `PersistentMemory`, `AutomationScheduler`, cognitive learning, Dream Memory, deterministic pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-continuous-agi-learning-loop-design.md`

## Global Constraints

- Active execution never mutates adaptive policy synchronously.
- Experience and policy history are append-only.
- Promotion requires independent evidence and holdout/transfer support.
- Policy changes are bounded and apply only to future rounds.
- Maintenance is durable, claim-based, bounded, and retryable.
- Hard safety, verification, quality, and orchestration limits are never tuned away.

---

### Task 1: Build empirical adaptive history and policy tuner

**Files:**
- Create: `portable/adaptive_tuning.py`
- Modify: `portable/__init__.py`
- Test: `tests/portable/test_adaptive_tuning.py`

**Interfaces:**
- `AdaptiveTuner.record_experience(...) -> ExperienceRecord`
- `AdaptiveTuner.history(scope='global', limit=200) -> tuple[ExperienceRecord, ...]`
- `AdaptiveTuner.current_policy(scope='global') -> AdaptivePolicy`
- `AdaptiveTuner.evaluate(scope, candidate_strategy=None) -> TuningDecision`
- `AdaptiveTuner.record_maintenance_receipt(...) -> MaintenanceReceipt`

- [x] Define frozen experience, policy, decision, and receipt records.
- [x] Persist immutable experience history, policy versions, and receipts in SQLite.
- [x] Require independent observation minimums before adaptation.
- [x] Add strategy promotion only when candidate benefit passes a holdout/transfer check.
- [x] Add bounded confidence calibration and iteration-target changes.
- [x] Add deterministic evidence digests for history and receipts.

### Task 2: Make adaptive maintenance durably scheduled

**Files:**
- Modify: `portable/automation_scheduler.py`
- Modify: `portable/adaptive_runtime.py`
- Test: `tests/portable/test_continuous_learning_runtime.py`

**Interfaces:**
- `AutomationScheduler.find_task(task) -> Schedule | None`
- `AdaptiveRuntime.ensure_learning_maintenance(project_root, interval_seconds=None)`
- `AdaptiveRuntime.maintenance_tick(project_root, budget=None) -> MaintenanceReceipt | None`
- `AdaptiveRuntime.recent_maintenance(project_root, limit=20)`
- `AdaptiveRuntime.current_adaptive_policy(project_root, scope='global')`

- [x] Add exact durable schedule lookup for idempotent registration.
- [x] Register one project learning schedule and make its first cycle immediately eligible.
- [x] Claim scheduled work before running the maintenance handler.
- [x] Bound the number of deferred jobs processed per cycle.
- [x] Record a receipt and use scheduler success/retryable completion semantics.

### Task 3: Feed every task round into the learning system

**Files:**
- Modify: `portable/adaptive_runtime.py`
- Test: `tests/portable/test_continuous_learning_runtime.py`

**Interfaces:**
- `AdaptiveRuntime.run(..., learning_strategy='default', learning_confidence=0.5)`

- [x] Snapshot the active policy before orchestration.
- [x] Expose that snapshot to the task as `auren_adaptive_policy`.
- [x] Persist immutable adaptive experience after both accepted and failed rounds.
- [x] Preserve the existing deferred cognitive-learning lane.
- [x] Keep policy adaptation outside the active execution path.

### Task 4: Verify and harden the PR

**Files:**
- Modify: only files required by review or verification findings.
- Test: complete repository suite and all required GitHub Actions workflows.

- [ ] Run the focused adaptive-learning, tuning, runtime, and scheduler tests through CI.
- [ ] Review the complete diff and changed-file patches for scope, determinism, isolation, and safety regressions.
- [ ] Fix all material review or CI findings and re-run every required check on the new head.
- [ ] Confirm all five repository-required workflows are green on the final PR head.
- [ ] Merge with the expected final head SHA and verify the merged `main` contents.
