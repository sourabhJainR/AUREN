# Cognitive Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the existing AUREN cognitive primitives with `AdaptiveRuntime.run()` so each execution produces a bounded cognitive episode, persists compact observations, and exposes evaluation evidence without replacing execution authority.

**Architecture:** `AdaptiveRuntime` remains the execution facade and `Orchestrator` remains authoritative for task execution. A per-execution `CognitiveLoop` wraps the lifecycle and uses the project-scoped `CognitiveRuntime` plus canonical `PersistentMemory`. Cognitive persistence is best-effort: persistence failures are captured in the receipt and never change orchestration success/failure semantics.

**Tech Stack:** Python 3, existing `portable` cognitive/runtime modules, pytest, SQLite-backed `PersistentMemory`.

**Spec:** Architectural design agreed in-chat for the Cognitive Closed Loop increment.

## Global Constraints

- Do not replace `StateGraph`, `Graph`, or `Orchestrator` as execution authority.
- Do not add paid-service dependencies.
- Keep cognitive state project-scoped through the canonical `PersistentMemory` path.
- Keep each cognitive episode bounded and serializable.
- Preserve existing `AdaptiveRuntime.run()` return type and failure semantics.
- Cognitive persistence failures must not fail or alter successful orchestration.
- Add regression coverage for persistence failure isolation.

---

### Task 1: Define the cognitive episode contract

**Files:**
- Create: `portable/cognitive_loop.py`
- Test: `tests/portable/test_cognitive_loop.py`

**Interfaces:**
- `CognitiveLoop.begin(...) -> CognitiveEpisode`
- `CognitiveLoop.observe(...) -> None`
- `CognitiveLoop.complete(...) -> CognitiveEpisodeReceipt`
- `CognitiveEpisodeReceipt` contains deterministic digest and persistence errors.

- [ ] **Step 1: Write the failing test**

```python
from portable.cognitive_loop import CognitiveEpisode


def test_episode_has_stable_identity_and_phase_history():
    episode = CognitiveEpisode.start("project-x", "task-1", "improve parser")
    episode.record_phase("observe")
    episode.record_phase("plan")
    receipt = episode.finish("success")
    assert receipt.project_key == "project-x"
    assert receipt.task_id == "task-1"
    assert receipt.status == "success"
    assert receipt.phases == ("observe", "plan")
    assert receipt.episode_id
    assert len(receipt.digest) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/portable/test_cognitive_loop.py -q`
Expected: FAIL because `portable.cognitive_loop` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `CognitiveEpisode`, `CognitiveEpisodeReceipt`, and `CognitiveLoop` with UTC timestamps, UUID episode IDs, phase deduplication, bounded observation copies, and a SHA-256 digest over canonical JSON.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/portable/test_cognitive_loop.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/cognitive_loop.py tests/portable/test_cognitive_loop.py
git commit -m "feat: add bounded cognitive episode contract"
```

### Task 2: Connect the loop to AdaptiveRuntime

**Files:**
- Modify: `portable/adaptive_runtime.py`
- Modify: `portable/__init__.py`
- Test: `tests/test_adaptive_runtime_context.py`

**Interfaces:**
- `AdaptiveRuntime.run()` still returns `OrchestrationRun`.
- `AdaptiveRuntime.last_cognitive_episode` exposes the latest receipt.
- A fresh `CognitiveLoop` is created inside each `run()` invocation to prevent concurrent executions from sharing mutable cognitive state.

- [ ] **Step 1: Write the failing integration test**

```python
def test_runtime_records_cognitive_episode_without_replacing_orchestrator():
    result = runtime.run(session_id="s1", task_id="t1", project_root=root, intent="Fix timeout")
    assert result.status.value == "accepted"
    assert runtime.last_cognitive_episode is not None
    assert runtime.last_cognitive_episode.task_id == "t1"
    assert runtime.last_cognitive_episode.status == result.status.value
    assert "evaluate" in runtime.last_cognitive_episode.phases
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adaptive_runtime_context.py -q`
Expected: FAIL because the runtime has no cognitive episode lifecycle.

- [ ] **Step 3: Implement the lifecycle**

Instantiate `CognitiveLoop(self.cognition(project_root))` after the session checkpoint is saved. Record `before_agent`, optional context resolution, and orchestration completion observations. Complete the receipt on success and in the exception path. Leave the existing hooks, checkpoint transitions, orchestrator call, return value, and raised exceptions intact.

- [ ] **Step 4: Add public exports**

Export `CognitiveEpisode`, `CognitiveEpisodeReceipt`, and `CognitiveLoop` from `portable.__init__`.

- [ ] **Step 5: Run regression tests**

Run: `pytest tests/test_adaptive_runtime_context.py -q tests/portable/test_cognitive_loop.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add portable/adaptive_runtime.py portable/__init__.py tests/test_adaptive_runtime_context.py
git commit -m "feat: integrate cognitive episode lifecycle into runtime"
```

### Task 3: Persist observations without taking execution authority

**Files:**
- Modify: `portable/cognitive_loop.py`
- Test: `tests/portable/test_cognitive_loop.py`
- Test: `tests/test_adaptive_runtime_context.py`

**Interfaces:**
- `CognitiveLoop(cognitive)` persists compact records through `cognitive.memory.remember(...)` with `approved=True` for this internal trusted runtime event stream.
- `_persist(...)` catches persistence exceptions and appends normalized diagnostic text to the episode.
- `CognitiveEpisodeReceipt.persistence_errors` exposes persistence failures without changing episode status.

- [ ] **Step 1: Write the persistence test**

```python
def test_loop_persists_episode_observation():
    memory = PersistentMemory(path, require_approval=False)
    cognitive = CognitiveRuntime.create(memory, "project-x")
    loop = CognitiveLoop(cognitive)
    episode = loop.begin("project-x", "task-1", "learn")
    loop.observe(episode, {"event": "execution_started", "status": "running"})
    assert memory.search("project-x", "execution_started", limit=5)
```

- [ ] **Step 2: Write the failure-isolation test**

```python
class FailingMemory(PersistentMemory):
    def remember(self, *args, **kwargs):
        raise RuntimeError("forced memory failure")


def test_cognitive_persistence_failure_does_not_change_success():
    memory = FailingMemory(path, require_approval=False)
    runtime = build_runtime(memory)
    result = runtime.run(session_id="s1", task_id="t2", project_root=root, intent="persist safely")
    assert result.status.value == "accepted"
    assert runtime.last_cognitive_episode.persistence_errors
    assert "forced memory failure" in runtime.last_cognitive_episode.persistence_errors[0]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/portable/test_cognitive_loop.py tests/test_adaptive_runtime_context.py -q`
Expected: the persistence test fails before persistence is added and the failure-isolation behavior is absent before exception capture is added.

- [ ] **Step 4: Implement bounded persistence**

Persist only episode ID, task ID, event name, status, phase, and an observation digest. Catch all persistence exceptions at this boundary, record them in `_persistence_errors`, and never re-raise them into `AdaptiveRuntime.run()`.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/portable/test_cognitive_loop.py tests/test_adaptive_runtime_context.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add portable/cognitive_loop.py tests/portable/test_cognitive_loop.py tests/test_adaptive_runtime_context.py
git commit -m "fix: isolate cognitive persistence failures"
```

### Task 4: PR review, CI, merge, and branch cleanup

**Files:**
- No source files unless review fixes are required.

**Interfaces:**
- The increment merges only when review findings are resolved and every CI check for the latest head SHA is complete and successful.

- [ ] **Step 1: Compare branch against `main` and inspect the complete diff.**
- [ ] **Step 2: Perform a code/design review and record all findings.**
- [ ] **Step 3: Fix all actionable findings and push the fixes.**
- [ ] **Step 4: Wait for all CI checks on the latest head SHA.**
- [ ] **Step 5: If any check fails, diagnose it, fix it, and repeat the full check cycle.**
- [ ] **Step 6: Merge the PR only after all checks are green.**
- [ ] **Step 7: Delete the feature branch.**
- [ ] **Step 8: Verify the repository has only `main`.**

After merge, start the next backlog increment from the resulting `main` SHA and repeat this lifecycle.
