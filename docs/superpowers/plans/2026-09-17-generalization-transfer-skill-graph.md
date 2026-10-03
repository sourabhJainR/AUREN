# Generalization, Transfer, and Skill Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend AUREN from exact-match learning transfer to reusable abstractions, structural analogy, negative-transfer guards, and a durable skill dependency graph.

**Architecture:** Keep `LearningTransfer.transfer()` as the evidence-gated exact-match baseline. Add a `GeneralizationEngine` that stores normalized structural abstractions and computes deterministic similarity/analogy candidates, and a `SkillGraph` that persists skills, prerequisites, evidence and failure modes. Generalized candidates remain recommendations until evidence gates validate them; no new permissions are granted by generalization.

**Tech Stack:** Python 3 standard library, existing SQLite-backed `PersistentMemory`, pytest/unittest style already used by the repository.

**Spec:** Architectural backlog increment approved in chat: Generalization & Abstraction, Skill Graph, and Strong Transfer.

## Global Constraints

- No paid-service dependencies.
- Exact-match verified transfer remains available and unchanged.
- Generalized transfer must be deterministic and evidence-aware.
- Negative-transfer conditions must be represented explicitly.
- Skill prerequisites must be cycle-safe and queryable.
- Generalization must not expand execution permissions.
- Use focused regression tests and preserve existing APIs.

---

### Task 1: Generalization abstraction store

**Files:**
- Create: `portable/generalization.py`
- Test: `tests/portable/test_generalization.py`

**Interfaces:**
- `Abstraction`
- `AnalogyCandidate`
- `GeneralizationEngine.record(...)`
- `GeneralizationEngine.find_analogies(...)`
- `GeneralizationEngine.validate_candidate(...)`

- [ ] **Step 1: Write failing tests**

```python
from portable.generalization import GeneralizationEngine, Abstraction


def test_structural_analogy_ranks_overlap_and_preserves_negative_conditions():
    engine = GeneralizationEngine(memory, "target")
    engine.record(Abstraction("a1", "retry-with-backoff", "network recovery", frozenset({"retry", "timeout", "backoff"}), ("e1",), 0.9,
                             frozenset({"non_idempotent_action"})))
    candidate = engine.find_analogies(frozenset({"retry", "timeout", "backoff"}), limit=5)[0]
    assert candidate.abstraction_id == "a1"
    assert candidate.similarity == 1.0
    assert "non_idempotent_action" in candidate.negative_conditions


def test_validation_requires_independent_evidence():
    engine = GeneralizationEngine(memory, "target")
    engine.record(Abstraction("a1", "parser-recovery", "recovery", frozenset({"retry", "validate"}), ("e1",), 0.8, frozenset()))
    candidate = engine.find_analogies(frozenset({"retry", "validate"}))[0]
    assert not engine.validate_candidate(candidate, evidence_ids=())
    assert not engine.validate_candidate(candidate, evidence_ids=("e1",))
    assert engine.validate_candidate(candidate, evidence_ids=("e2",))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/portable/test_generalization.py -q`
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the minimal persistent abstraction model**

Use SQLite tables `generalization_abstractions` and `generalization_validations`, normalize structure features as sorted unique strings, compute Jaccard similarity, carry source evidence and negative conditions, reject empty structural evidence, and require validation evidence distinct from source evidence.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/portable/test_generalization.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/generalization.py tests/portable/test_generalization.py
git commit -m "feat: add structural generalization and analogy engine"
```

### Task 2: Durable skill graph

**Files:**
- Create: `portable/skill_graph.py`
- Test: `tests/portable/test_skill_graph.py`

**Interfaces:**
- `SkillNode`
- `SkillGraph.upsert(...)`
- `SkillGraph.add_dependency(...)`
- `SkillGraph.ready(...)`
- `SkillGraph.missing_prerequisites(...)`
- `SkillGraph.ancestors(...)`

- [ ] **Step 1: Write failing tests**

```python
from portable.skill_graph import SkillGraph, SkillNode


def test_skill_graph_tracks_prerequisites_and_readiness():
    graph = SkillGraph(memory, "project-x")
    graph.upsert(SkillNode("parse", "skill", frozenset(), frozenset({"e1"}), frozenset({"parse_error"}), frozenset(), True))
    graph.upsert(SkillNode("recover", "skill", frozenset({"parse"}), frozenset({"e2"}), frozenset({"bad_retry"}), frozenset(), False))
    assert graph.ready("parse")
    assert not graph.ready("recover")
    assert graph.missing_prerequisites("recover") == ("parse",)


def test_skill_graph_rejects_dependency_cycles():
    graph = SkillGraph(memory, "project-x")
    graph.upsert(SkillNode("a", "skill"))
    graph.upsert(SkillNode("b", "skill"))
    graph.add_dependency("b", "a")
    with pytest.raises(ValueError):
        graph.add_dependency("a", "b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/portable/test_skill_graph.py -q`
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement persistent skill graph**

Store nodes and directed prerequisite edges under project scope. Reject unknown prerequisite nodes. Detect dependency cycles with bounded DFS before insertion, and preserve prior dependencies when an `upsert()` cycle check fails. `ready()` is true only when the node itself is validated with evidence and all prerequisites are validated with evidence.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/portable/test_skill_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/skill_graph.py tests/portable/test_skill_graph.py
git commit -m "feat: add durable skill dependency graph"
```

### Task 3: Strong transfer over structural similarity

**Files:**
- Modify: `portable/learning_transfer.py`
- Test: `tests/test_learning_transfer.py`

**Interfaces:**
- Add optional `structure_signature: tuple[str, ...] = ()` and `negative_conditions: tuple[str, ...] = ()` to `LearningExperience` without breaking existing constructors through defaults.
- `LearningTransfer.transfer_structural(...) -> list[TransferCandidate]`
- Structural transfer ranks by similarity, independent source-project count, and confidence.
- Negative-transfer rules exclude candidates whose negative conditions intersect supplied target conditions.
- Re-recording an existing experience ID with different transfer metadata is rejected.

- [ ] **Step 1: Write failing tests**

```python
def test_structural_transfer_finds_related_verified_experience():
    transfer = LearningTransfer(memory, "target")
    transfer.record(LearningExperience("e1", "source-a", "parser", "recovery", "worked", "retry after timeout",
                                       ("ev1",), 0.9, True, ("retry", "timeout")))
    candidates = transfer.transfer_structural("parser", "recovery", ("retry", "timeout", "backoff"))
    assert candidates
    assert candidates[0].detail == "retry after timeout"


def test_structural_transfer_rejects_negative_context():
    transfer.record(LearningExperience("e2", "source-a", "parser", "recovery", "worked", "retry safely",
                                       ("ev2",), 0.9, True, ("retry",), ("non_idempotent_action",)))
    assert transfer.transfer_structural("parser", "recovery", ("retry",),
                                        target_conditions=("non_idempotent_action",)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_learning_transfer.py -q`
Expected: FAIL because structural signature transfer is unavailable.

- [ ] **Step 3: Implement structural matching**

Persist signatures in a companion `learning_transfer_signatures` table keyed by experience ID. Use Jaccard similarity over signatures, aggregate equivalent patterns across independent source projects, reuse existing verified/worked/evidence filters, and attach negative conditions to each returned candidate.

- [ ] **Step 4: Run regression tests**

Run: `pytest tests/test_learning_transfer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/learning_transfer.py tests/test_learning_transfer.py
git commit -m "feat: add structural evidence-gated learning transfer"
```

### Task 4: Public exports and integration regression

**Files:**
- Modify: `portable/__init__.py`
- Test: `tests/portable/test_generalization.py`
- Test: `tests/portable/test_skill_graph.py`

- [ ] **Step 1: Add public exports**
- [ ] **Step 2: Run the focused generalized-learning suite**

Run: `pytest tests/portable/test_generalization.py tests/portable/test_skill_graph.py tests/test_learning_transfer.py -q`
Expected: PASS.

- [ ] **Step 3: Run the repository regression suite defined by CI**
- [ ] **Step 4: Commit**

```bash
git add portable/__init__.py tests/portable/test_generalization.py tests/portable/test_skill_graph.py tests/test_learning_transfer.py
# include any required integration changes in this commit
git commit -m "feat: export generalization and skill graph capabilities"
```

### Task 5: PR review, CI, merge, and branch cleanup

**Files:**
- No source files unless review fixes are required.

- [ ] **Step 1: Inspect complete PR diff and review design/correctness.**
- [ ] **Step 2: Fix every actionable review finding.**
- [ ] **Step 3: Wait for every CI workflow on the latest head SHA.**
- [ ] **Step 4: Fix any failed check and repeat the complete CI cycle.**
- [ ] **Step 5: Merge only after every check is successful.**
- [ ] **Step 6: Delete feature branch when the repository tooling permits it.**
- [ ] **Step 7: Verify `main` contains the merged change.**

After merge, begin the next backlog increment from the resulting `main` SHA.