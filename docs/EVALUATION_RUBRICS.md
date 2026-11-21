# Versioned evaluation rubrics

AUREN uses a rubric as a versioned evaluation contract, not as an execution
authority. The initial Python contract is `portable.evaluation_rubric`.

## Contract

- Every rubric has a stable ID, semantic version, schema version and canonical
  SHA-256 digest. The standard engineering-delivery profile includes requirement,
  correctness, verification, security, reliability, maintainability and evidence
  criteria with independent Guard, test, approval, provenance and rollback gates.
- A plan binding pins plan ID/digest, canonical task IDs and rubric digest before
  execution; attach this binding to the canonical plan/run manifest.
- Criterion IDs are stable. Weights sum to 100. Each scored criterion needs
  criterion-specific evidence references. Scoped criteria require an explicit
  applicability decision. A not-applicable decision needs a rationale, cannot
  waive a mandatory criterion, carries no score/evidence, and is excluded from
  the weighted denominator; remaining weights are normalized to 100.
- A missing score or evidence reference is `unscored`, not zero and never pass.
- Mandatory policy gates are independent of the weighted score. A failed or
  unknown gate prevents a passing result even when the weighted score is 100.
- The executor cannot evaluate its own candidate when its identity is supplied.
- Receipts bind the rubric digest, exact source and plan digests, canonical
  task IDs, artifact digests, subject, run, executor identity, distinct evaluator
  identity/version, per-criterion applicability, rationale and uncertainty.
  `rubric_receipt_projection` provides a task-scoped projection for CLI, MCP and
  operator workbench consumers; a task outside the receipt is rejected. Missing rationale/evidence remains unscored. Re-scoring
  under a changed rubric creates a new receipt; prior receipts are not mutated.

## Example

```python
from portable.evaluation_rubric import (
    RubricCriterion, RubricDefinition, evaluate_rubric,
)

rubric = RubricDefinition(
    rubric_id="engineering-delivery",
    version="1.0.0",
    criteria=(
        RubricCriterion("correctness", "Acceptance behavior", 50, ("tests",)),
        RubricCriterion("evidence", "Evidence quality", 50, ("report",)),
    ),
    hard_gate_ids=("guard", "required_ci"),
)
receipt = evaluate_rubric(
    rubric,
    subject_id="candidate-1",
    run_id="run-1",
    source_digest="<exact source digest>",
    plan_digest="<exact plan digest>",
    artifact_digests=("<artifact digest>",),
    evaluator_id="independent-qa",
    evaluator_version="qa-v1",
    executor_id="implementer",
    task_ids=("task-1",),
    scores={"correctness": 100, "evidence": 95},
    evidence={"correctness": ["tests://run-1"], "evidence": ["report://run-1"]},
    rationales={"correctness": "Acceptance tests pass", "evidence": "Report verified"},
    hard_gates={"guard": True, "required_ci": True},
)
```

## Trust boundary

The module validates local contracts and creates deterministic receipts. It
does not prove that evidence URLs resolve, that a test ran, that an evaluator is
independent in deployment, or that an operator controls a sealed holdout. Those
claims require trusted evidence verification and external custody. Rubric
scores cannot authorize dispatch, merge, deployment, promotion or bypass Guard.

A passing local receipt is a quality signal only. Canonical delivery gates and repository protection rules remain independently required.
