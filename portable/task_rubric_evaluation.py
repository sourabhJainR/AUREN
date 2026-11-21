"""Task-isolated versioned rubric evaluation.

Each task receives its own receipt and scoring inputs. Aggregating task-level
receipts is intentionally left to consumers; a multi-task plan must not reuse
one task's score or applicability decisions for another task.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .evaluation_rubric import RubricDefinition, RubricScoreReceipt, evaluate_rubric


def evaluate_task_scoped_rubrics(
    rubric: RubricDefinition,
    *,
    task_evaluations: Mapping[str, Mapping[str, Any]],
    subject_id: str,
    run_id: str,
    source_digest: str,
    plan_digest: str,
    executor_id: str,
    evaluator_id: str,
    evaluator_version: str,
    artifact_digests: Sequence[str],
) -> tuple[RubricScoreReceipt, ...]:
    """Create one independently scored receipt per canonical task.

    Each task entry must provide its own scores, evidence, hard_gates and may
    provide rationales, applicability, applicability_rationales, uncertainty.
    The map keys are the canonical task IDs and must be unique/non-empty.
    """
    if not isinstance(task_evaluations, Mapping) or not task_evaluations:
        raise ValueError("task_evaluations must be a non-empty task mapping")
    task_ids = tuple(task_evaluations.keys())
    if any(not isinstance(task_id, str) or not task_id.strip() for task_id in task_ids):
        raise ValueError("task IDs must be non-empty strings")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("task IDs must be unique")
    required = {"scores", "evidence", "hard_gates"}
    receipts: list[RubricScoreReceipt] = []
    for task_id, data in task_evaluations.items():
        if not isinstance(data, Mapping) or not required.issubset(data):
            raise ValueError(f"task {task_id} requires scores, evidence and hard_gates")
        receipts.append(evaluate_rubric(
            rubric,
            subject_id=subject_id,
            run_id=run_id,
            source_digest=source_digest,
            plan_digest=plan_digest,
            executor_id=executor_id,
            evaluator_id=evaluator_id,
            evaluator_version=evaluator_version,
            task_ids=(task_id,),
            artifact_digests=artifact_digests,
            scores=data["scores"],
            evidence=data["evidence"],
            hard_gates=data["hard_gates"],
            rationales=data.get("rationales"),
            applicability=data.get("applicability"),
            applicability_rationales=data.get("applicability_rationales"),
            uncertainty=data.get("uncertainty", 0.0),
        ))
    return tuple(receipts)


__all__ = ["evaluate_task_scoped_rubrics"]
