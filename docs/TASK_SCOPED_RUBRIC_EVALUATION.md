## Task-isolated rubric scoring

Use `portable.task_rubric_evaluation.evaluate_task_scoped_rubrics` when a plan
contains multiple canonical tasks that need separate evaluation. Supply one
entry per task, including that task's scores, evidence, hard gates and optional
rationales/applicability decisions. The function returns a separate immutable
receipt per task, with a singleton task ID. Consumers must not copy one task's
score or applicability decisions into another task's receipt.

This is task-scoped scoring, not proof of evaluator independence or holdout
custody. Those remain separate trust requirements. Existing aggregate rubric
evaluation remains available for legacy single-scope workflows.
