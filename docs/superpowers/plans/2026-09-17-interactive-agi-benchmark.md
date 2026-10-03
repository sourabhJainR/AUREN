# Interactive AGI Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an executable, hidden-task-friendly benchmark harness that measures interactive planning, recovery, tool use, long-horizon behavior, and transfer/generalization rather than comparing only pre-supplied expected/observed strings.

**Architecture:** `InteractiveBenchmark` runs a caller-provided policy against deterministic sandbox environments. Each environment exposes only an observation and a finite action set; the policy must choose actions, receives resulting observations, and may fail/recover within bounded steps. Benchmark suites support visible training tasks and hidden evaluation tasks generated from the same family with held-out parameters, and report transfer/generalization separately from training performance.

**Tech Stack:** Python standard library and existing AUREN deterministic evaluation primitives.

**Spec:** Approved backlog increment: executable AGI benchmark environments with hidden interactive tasks and generalization measurement.

## Global Constraints
- No paid dependencies.
- No network or external side effects from built-in benchmark environments.
- Hidden task parameters are not exposed to the policy.
- Every episode has bounded steps and deterministic seeds.
- Benchmark policy receives observations/actions only through the environment interface.
- Training and hidden evaluation metrics are separated.

---

### Task 1: Deterministic environment protocol and runner
**Files:** `portable/interactive_benchmark.py`; `tests/portable/test_interactive_benchmark.py`
- [ ] Test reset, bounded stepping, invalid action handling, and deterministic replay.
- [ ] Implement `InteractiveEnvironment` protocol, `EpisodeResult`, `InteractiveBenchmark.run_episode(...)`.
- [ ] Record action trace, observation trace, reward, success, steps, recovery count and digest.
- [ ] Commit: `feat: add executable interactive benchmark harness`

### Task 2: Hidden transfer benchmark suite
**Files:** `portable/interactive_benchmark.py`; `tests/portable/test_interactive_benchmark.py`
- [ ] Add deterministic `KeyDoorEnvironment` family where policy must infer a hidden key/action sequence from observations.
- [ ] Expose training and hidden tasks with held-out layouts/seeds.
- [ ] Ensure hidden parameters never appear in policy observations.
- [ ] Report training success and hidden transfer success separately.
- [ ] Commit: `feat: add hidden interactive transfer benchmark`

### Task 3: Generalization and recovery metrics
**Files:** `portable/interactive_benchmark.py`; `tests/portable/test_interactive_benchmark.py`
- [ ] Add metrics for hidden transfer rate, recovery rate, efficiency, and generalization gap.
- [ ] Support optional evaluator callbacks for policy confidence/calibration and evidence.
- [ ] Ensure deterministic benchmark digest covers hidden suite definitions without revealing hidden values in episode traces.
- [ ] Commit: `feat: add interactive benchmark transfer metrics`

### Task 4: Public exports, review, CI, merge
- [ ] Export benchmark types.
- [ ] Inspect complete diff and review all safety/measurement invariants.
- [ ] Fix every actionable review finding.
- [ ] Wait for every CI workflow on latest head SHA.
- [ ] Fix failures and repeat full CI cycle.
- [ ] Merge after all workflows succeed.
- [ ] Delete feature branch when tooling permits it.
- [ ] Verify merged main.
