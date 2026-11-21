from portable.evaluation_rubric import engineering_delivery_rubric, rubric_receipt_projection
from portable.task_rubric_evaluation import evaluate_task_scoped_rubrics


def _inputs(task_scores):
    evidence = {key: [kind + "://evidence"] for key, kind in [
        ("requirements", "requirements"), ("correctness", "tests"),
        ("verification", "verification"), ("security", "security"),
        ("reliability", "reliability"), ("maintainability", "review"),
        ("evidence", "evidence"),
    ]}
    rationale = {key: "checked for task" for key in task_scores}
    gates = {"guard": True, "required_tests": True, "approvals": True,
             "provenance": True, "rollback": True}
    return {"scores": task_scores, "evidence": evidence, "rationales": rationale,
            "hard_gates": gates}


def test_tasks_receive_separate_receipts_and_scores():
    rubric = engineering_delivery_rubric()
    full = {c.criterion_id: 100 for c in rubric.criteria}
    low = dict(full)
    low["correctness"] = 40
    evaluations = {"task-alpha": _inputs(full), "task-beta": _inputs(low)}
    receipts = evaluate_task_scoped_rubrics(
        rubric, task_evaluations=evaluations, subject_id="subject",
        run_id="run", source_digest="a" * 64, plan_digest="b" * 64,
        executor_id="executor", evaluator_id="evaluator",
        evaluator_version="1", artifact_digests=("c" * 64,),
    )
    assert len(receipts) == 2
    assert receipts[0].task_ids == ("task-alpha",)
    assert receipts[1].task_ids == ("task-beta",)
    assert receipts[0].weighted_score != receipts[1].weighted_score
    assert rubric_receipt_projection(receipts[0], task_id="task-alpha")["task_id"] == "task-alpha"


def test_task_projection_cannot_cross_receipt_task_scope():
    rubric = engineering_delivery_rubric()
    scores = {c.criterion_id: 100 for c in rubric.criteria}
    receipt = evaluate_task_scoped_rubrics(
        rubric, task_evaluations={"task-alpha": _inputs(scores)},
        subject_id="subject", run_id="run", source_digest="a" * 64,
        plan_digest="b" * 64, executor_id="executor", evaluator_id="evaluator",
        evaluator_version="1", artifact_digests=("c" * 64,),
    )[0]
    try:
        rubric_receipt_projection(receipt, task_id="task-beta")
    except ValueError:
        pass
    else:
        raise AssertionError("cross-task projection must be rejected")


def test_task_mapping_must_have_required_fields():
    rubric = engineering_delivery_rubric()
    try:
        evaluate_task_scoped_rubrics(
            rubric, task_evaluations={"task-alpha": {"scores": {}}},
            subject_id="subject", run_id="run", source_digest="a" * 64,
            plan_digest="b" * 64, executor_id="executor", evaluator_id="evaluator",
            evaluator_version="1", artifact_digests=("c" * 64,),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("incomplete task entry must be rejected")
