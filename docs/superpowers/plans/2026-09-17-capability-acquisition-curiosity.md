# Capability Acquisition and Curiosity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn AUREN capability acquisition into an evidence-gated practice/evaluation/graduation loop, and add a deterministic planner for selecting the next useful learning target.

**Architecture:** `CapabilityAcquirer` remains permission-neutral. Practice uses a caller-owned bounded runner and persists its result. Graduation accepts only persisted successful attempts plus independent evidence and records a validated skill in `SkillGraph`. `CuriosityEngine` ranks needs but never executes them.

**Tech Stack:** Python standard library, existing SQLite-backed `PersistentMemory`, existing `SkillGraph`, unittest/pytest.

**Spec:** Approved backlog increment: capability acquisition loop and open-ended curiosity.

## Global Constraints
- No paid-service dependencies.
- Capability acquisition cannot grant permissions or bypass risk controls.
- Practice is caller-supplied and bounded.
- Graduation requires repeated persisted success and independent evidence.
- Curiosity is a planner, not an executor.
- Existing `propose()` and `validate()` behavior remains compatible.
- Preserve the existing public `GraduationReceipt` exported from autonomy graduation.

---

### Task 1: Capability practice and graduation

**Files:** `portable/capability_acquisition.py`; `tests/portable/test_capability_acquisition.py`

**Interfaces:** `PracticeResult`; `GraduationReceipt`; `CapabilityAcquirer.practice(proposal_id, runner)`; `CapabilityAcquirer.graduate(proposal_id, results, evidence_ids)`.

- [ ] Add tests for successful bounded practice, runner failure, repeated-success graduation, and rejection of fabricated practice results.
- [ ] Implement persisted `capability_practice` records with bounded diagnostic text.
- [ ] Require two distinct persisted accepted attempts and two graduation evidence IDs before graduation.
- [ ] On graduation, add the capability as a validated `SkillNode` without granting execution permissions.
- [ ] Run: `pytest tests/portable/test_capability_acquisition.py -q`
- [ ] Commit with: `feat: add evidence-gated capability practice and graduation`

### Task 2: Curiosity learning frontier

**Files:** `portable/curiosity.py`; `tests/portable/test_curiosity.py`

**Interfaces:** `LearningNeed`; `LearningChoice`; `CuriosityEngine.rank(needs)`; `CuriosityEngine.choose(needs, max_cost, max_risk)`.

- [ ] Test high-value uncertainty prioritization, risk/budget filtering, and deterministic ties.
- [ ] Implement score = uncertainty × importance × expected gain / cost × (1 - risk).
- [ ] Exclude candidates above cost/risk limits and never execute external work.
- [ ] Run: `pytest tests/portable/test_curiosity.py -q`
- [ ] Commit with: `feat: add deterministic curiosity learning frontier`

### Task 3: Public exports and integration

**Files:** `portable/__init__.py`; `tests/portable/test_capability_acquisition.py`

- [ ] Preserve autonomy graduation as the public `GraduationReceipt` symbol.
- [ ] Export capability graduation as `CapabilityGraduationReceipt`.
- [ ] Export `PracticeResult`, `CuriosityEngine`, `LearningNeed`, and `LearningChoice`.
- [ ] Verify a graduated skill is reported ready by `SkillGraph.ready()`.
- [ ] Run: `pytest tests/portable/test_capability_acquisition.py tests/portable/test_curiosity.py tests/portable/test_skill_graph.py -q`
- [ ] Run the complete repository CI suite on the PR head.
- [ ] Commit with: `feat: export capability acquisition and curiosity primitives`

### Task 4: PR review, CI, merge, and branch cleanup

- [ ] Inspect the complete PR diff and review all behavior/invariants.
- [ ] Fix every actionable review finding.
- [ ] Wait for every CI workflow on the latest head SHA.
- [ ] Fix failed checks and repeat the full CI cycle.
- [ ] Merge only after every workflow succeeds.
- [ ] Delete the feature branch when repository tooling permits it.
- [ ] Verify the merged increment is present on `main`.
