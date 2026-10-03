# Belief-Aware Cognitive Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed explicit belief context into cognitive plans and persist post-execution learning signals so future plans can incorporate evidence from prior episodes.

**Architecture:** Preserve the existing `HypothesisEngine` as the authority for hypothesis/belief state. Add `CognitiveLearningLoop` as a narrow adapter that accepts caller-selected belief IDs/statements, creates a bounded plan context, and persists outcome evidence. `AdaptiveRuntime.run()` records the outcome through this adapter after execution; it does not invent hypotheses or grant authority.

**Tech Stack:** Python standard library, existing `PersistentMemory`, `SelfModel`, `CognitiveController`, pytest/unittest.

**Spec:** Final backlog increment: belief-informed cognitive loop plus post-action learning feedback.

## Global Constraints
- No paid dependencies.
- Existing hypothesis/causal/world/self-model APIs remain authoritative.
- Beliefs are advisory context, never execution authority.
- Outcome learning is durable, bounded, project-scoped, and deterministic.
- Failed persistence cannot change orchestration result.

---

### Task 1: Belief-aware planning adapter
**Files:** `portable/cognitive_learning.py`, `portable/cognitive_controller.py`, `tests/portable/test_cognitive_learning.py`
- [ ] Add `BeliefContext`, `LearningSignal`, and `CognitiveLearningLoop`.
- [ ] Normalize belief statements/confidence and rank by uncertainty.
- [ ] Persist learning signals with bounded evidence text and deterministic digests.
- [ ] Extend `CognitiveController.plan/enrich_context` with optional belief contexts and include them in `auren_cognitive_plan`.
- [ ] Commit: `feat: add belief-aware cognitive learning adapter`

### Task 2: Post-execution learning feedback
**Files:** `portable/adaptive_runtime.py`, `portable/cognitive_learning.py`, `tests/portable/test_cognitive_learning.py`
- [ ] After successful or failed orchestration, record an immutable learning signal.
- [ ] Record outcome in SelfModel with task context when capability is supplied.
- [ ] Ensure learning persistence errors are captured and cannot alter run result.
- [ ] Commit: `feat: feed execution outcomes into cognitive learning loop`

### Task 3: Public exports and tests
**Files:** `portable/__init__.py`, `tests/portable/test_cognitive_learning.py`
- [ ] Export new learning types.
- [ ] Test belief context appears in plan, success/failure feedback is durable, and persistence failures are isolated.
- [ ] Run: `pytest tests/portable/test_cognitive_learning.py -q`
- [ ] Commit: `test: cover belief-aware cognitive learning feedback`

### Task 4: PR review, CI, merge
- [ ] Review the complete diff.
- [ ] Fix every actionable review finding.
- [ ] Wait for every CI workflow on latest head SHA.
- [ ] Fix failures and repeat full CI cycle.
- [ ] Merge only after all workflows succeed.
- [ ] Delete feature branch when tooling permits it.
- [ ] Verify merged main.
