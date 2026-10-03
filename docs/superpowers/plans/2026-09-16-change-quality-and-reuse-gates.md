# Change Quality and Reuse Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing AUREN engineering-design gate so future repository work explicitly reuses maintained implementations, preserves usage patterns, controls data-access and performance cost, follows local logging/error conventions, and verifies regressions without adding a competing workflow.

**Architecture:** Extend `portable.engineering_design_guard.EngineeringDesignGuard` as the single deterministic quality/design gate. Update the canonical policy and deployable orchestration instructions so these evidence fields are part of normal implementation planning, while keeping all existing APIs and workflow sequencing unchanged.

**Tech Stack:** Python standard library, existing AUREN guard/tests, Markdown policy/skill documents, GitHub Actions already used by the repository.

**Spec:** `docs/superpowers/specs/2026-09-16-change-quality-and-reuse-design.md`

## Global Constraints

- Preserve existing public/runtime usage patterns unless the requested feature explicitly requires a breaking change.
- Reuse existing repository implementations and canonical stores before introducing new code or abstractions.
- Do not introduce a second planner, workflow engine, repository index, evidence store, logging abstraction, or quality database.
- Keep DB/data-access calls minimal through reuse, batching, caching where already established, and N+1 avoidance.
- Keep performance at repository baseline unless the requested behavior changes it explicitly.
- Follow repository-native logging, telemetry, exception propagation, cleanup, and redaction conventions.
- Add proportionate regression coverage and run repository-native verification before completion.
- No new third-party runtime dependency.

---

### Task 1: Extend the canonical engineering-design gate

**Files:**
- Modify: `portable/engineering_design_guard.py`
- Test: `tests/test_engineering_design_guard.py`

**Interfaces:**
- Consumes: existing `EngineeringDesignGuard.review(...)`, `DesignDimension`, `DesignFinding`, `DesignReviewReceipt`.
- Produces: the same public interfaces plus additional findings when quality evidence is omitted; no caller-facing signature break.

- [ ] **Step 1: Write focused failing tests**

Add tests proving that high-risk data-backed work must declare reuse, DB/query access semantics, performance expectations, exception handling, logging/observability, and regression safety, while explicit `not_applicable: reason` remains valid.

Example:

```python
def test_high_risk_change_requires_quality_contracts():
    receipt = EngineeringDesignGuard.review(
        intent="Optimize invoice lookup and add fallback behavior",
        changed_paths=["repository/invoice_repository.py"],
        risk="high",
        design={"architecture": "reuse existing invoice repository boundary"},
    )

    messages = [f.message for f in receipt.findings if f.severity == FindingSeverity.BLOCKING]
    assert any("reuse" in m.lower() for m in messages)
    assert any("regression" in m.lower() for m in messages)
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python -m unittest tests.test_engineering_design_guard -v`
Expected: the new assertions fail because the guard does not yet enforce the new evidence.

- [ ] **Step 3: Add reuse/performance/data-access/observability/error/regression evidence keys**

Extend the existing guard dimension key map rather than creating a new gate. Treat these keys as aliases under the existing dimensions:

```python
DesignDimension.DATA: (
    "data", "source_of_truth", "consistency", "idempotency", "ordering",
    "db_access", "query_pattern", "n_plus_one",
),
DesignDimension.CONSTRUCTION: (
    "construction", "validation", "error_handling", "test",
    "exception_handling", "logging", "regression", "reuse",
),
DesignDimension.RESILIENCE: (
    "resilience", "timeout", "retry", "recovery", "observability", "performance",
),
```

Add keyword/path inference for `query`, `sql`, `db`, `database`, `n+1`, `latency`, `throughput`, `allocation`, `logging`, `exception`, `error`, `reuse`, `existing implementation`, `regression`, `compatibility` so relevant dimensions are selected without requiring users to learn internal field names.

- [ ] **Step 4: Enforce high-risk omissions as blocking findings**

For `risk in {"high", "critical"}`, require the relevant evidence or an explicit `not_applicable: reason`. Keep low/medium risk behavior compatible by emitting warnings for omissions. Map omissions to existing dimensions so downstream receipt consumers remain unchanged.

- [ ] **Step 5: Preserve deterministic receipt behavior**

Keep the existing SHA-256 receipt shape and sort declared data before hashing. Do not add timestamps, environment-specific values, or nondeterministic collections.

- [ ] **Step 6: Run focused tests and existing guard tests**

Run: `python -m unittest tests.test_engineering_design_guard -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add portable/engineering_design_guard.py tests/test_engineering_design_guard.py
git commit -m "feat: enforce change quality and reuse evidence"
```

---

### Task 2: Make the canonical engineering policy explicit

**Files:**
- Modify: `.ai-harness/ENGINEERING_DESIGN_POLICY.md`
- Modify: `architecture/architecture.yaml`

**Interfaces:**
- Consumes: canonical engineering-design gate and architecture contract.
- Produces: policy evidence describing reuse-first, data-access economy, performance parity, repo-native observability/error behavior, and regression safety.

- [ ] **Step 1: Add a “Change Quality and Reuse” section**

State the exact required behaviors from the spec: search before create, preserve usage patterns, minimize DB/remote calls, maintain performance, reuse logging/error conventions, and verify adjacent behavior.

- [ ] **Step 2: Document explicit `not_applicable` semantics**

Make clear that a concern may be irrelevant, but the run must record why rather than silently omitting the dimension.

- [ ] **Step 3: Record this as policy, not a new architecture owner**

Update the architecture contract’s principles/forbidden-patterns only as needed to make “reuse before duplicate implementation” explicit. Do not add a new canonical domain.

- [ ] **Step 4: Run architecture validation**

Run: `python scripts/validate_architecture.py`
Expected: PASS with no hard failures.

- [ ] **Step 5: Commit**

```bash
git add .ai-harness/ENGINEERING_DESIGN_POLICY.md architecture/architecture.yaml
git commit -m "docs: formalize reuse and change quality policy"
```

---

### Task 3: Update the deployable orchestrator behavior

**Files:**
- Modify: `skills/ai-coding-orchestrator/SKILL.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: existing repository-map-first workflow and engineering-design guard.
- Produces: explicit operational instructions for reuse discovery, DB/access checks, performance checks, logging/error convention reuse, and regression verification.

- [ ] **Step 1: Add a reuse-first pre-implementation rule**

Require the orchestrator to search for comparable implementations, extension points, tests, helpers, repositories, clients, query paths, logging patterns, and exception types before adding new constructs.

- [ ] **Step 2: Add a quality evidence checklist to normal coding**

For relevant tasks, require the run to capture:

```text
reuse candidates -> usage compatibility -> data/DB access -> performance -> logging/telemetry -> exception handling -> regression safety net
```

- [ ] **Step 3: Add a minimal-access rule**

State that the agent should prefer already-fetched state, batching, existing caches, and established data-access paths and explicitly avoid introducing N+1 calls or repeated remote/database access.

- [ ] **Step 4: Keep workflow/usage compatibility explicit**

Require validation that existing APIs, command flows, lifecycle ordering, persisted contracts, and user-facing behavior remain unchanged unless the task explicitly asks otherwise.

- [ ] **Step 5: Preserve the existing single-agent-first flow**

Do not add another execution stage or recursion loop; enrich the existing implementation/review/verification gates.

- [ ] **Step 6: Commit**

```bash
git add skills/ai-coding-orchestrator/SKILL.md AGENTS.md
git commit -m "docs: enforce reuse-first engineering workflow"
```

---

### Task 4: Add targeted regression coverage for policy behavior

**Files:**
- Modify: `tests/test_engineering_design_guard.py`
- Create: `tests/test_change_quality_policy.py`

**Interfaces:**
- Consumes: guard and text-level policy contracts.
- Produces: deterministic regression coverage preventing accidental removal of the new requirements.

- [ ] **Step 1: Add guard tests for medium-risk warnings and explicit N/A**

Verify missing quality evidence warns at medium risk and `not_applicable: reason` suppresses the missing-evidence warning for a relevant practice.

- [ ] **Step 2: Add a policy regression test**

Read the canonical policy and deployable skill files and assert required phrases/anchors exist, including reuse-first, minimal data access, performance, logging, exception handling, and regression verification. Keep this test structural rather than depending on prose wording more than necessary.

- [ ] **Step 3: Run the focused regression suite**

Run: `python -m unittest tests.test_engineering_design_guard tests.test_change_quality_policy -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_engineering_design_guard.py tests/test_change_quality_policy.py
git commit -m "test: protect change quality policy"
```

---

### Task 5: Final repository-native verification and PR review

**Files:**
- Modify: none unless a verification issue requires a targeted fix.

**Interfaces:**
- Consumes: all prior changes and repository CI.
- Produces: verified PR ready for merge only when the complete matrix is green.

- [ ] **Step 1: Inspect final diff for scope creep**

Verify only the guard, policy, architecture contract, orchestrator instructions, and focused tests changed; reject unrelated refactors or new dependencies.

- [ ] **Step 2: Run targeted checks locally**

Run:

```bash
python -m unittest tests.test_engineering_design_guard tests.test_change_quality_policy -v
python scripts/validate_architecture.py
```

Expected: PASS.

- [ ] **Step 3: Open a pull request against `main`**

Document that runtime execution semantics are unchanged and that the new behavior is evidence/gate enforcement on future coding tasks.

- [ ] **Step 4: Inspect every PR check, not only one representative check**

Wait for all checks to complete. Treat any failure as unresolved, inspect the failing job, make the smallest corrective change, and re-run the complete relevant evidence loop.

- [ ] **Step 5: Verify merge and post-merge CI**

After merge, confirm `main` points to the merge commit and the repository-native post-merge checks succeed before reporting completion.
