from pathlib import Path
from portable.execution_controller import ExecutionController

def durable_handler(payload):
    return {"success": True, "detail": "done", "evidence": [payload["id"]]}

def test_handler_registration_submit_and_resume(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.durable_handler", durable_handler)
    task = controller.submit("tests.durable_handler", {"id": "e1"})
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "completed"

def test_session_mapping_survives_new_controller(tmp_path: Path):
    first = ExecutionController(tmp_path)
    work_id = first.work_unit_for_session("session-1", "long task")
    second = ExecutionController(tmp_path)
    assert second.work_unit_for_session("session-1", "long task") == work_id


def ci_gate_handler(payload):
    return payload["result"]


def test_ci_gated_task_rejects_stale_head_and_persists_failure(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": True,
            "expected_head_sha": "new-head",
            "result": {
                "success": True,
                "detail": "change verified",
                "ci_gate": {
                    "head_sha": "old-head",
                    "status": "completed",
                    "conclusion": "success",
                    "required_checks": ["tests"],
                    "passed_checks": ["tests"],
                },
            },
        },
    )
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "failed"


def test_ci_gated_task_rejects_missing_or_skipped_required_check(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": True,
            "expected_head_sha": "head-1",
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "head-1",
                    "status": "completed",
                    "conclusion": "success",
                    "required_checks": ["tests", "lint"],
                    "passed_checks": ["tests"],
                    "skipped_checks": ["lint"],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gated_task_completes_only_for_exact_head_and_all_required_checks(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": True,
            "expected_head_sha": "head-2",
            "result": {
                "success": True,
                "detail": "change verified",
                "ci_gate": {
                    "head_sha": "head-2",
                    "status": "completed",
                    "conclusion": "success",
                    "required_checks": ["tests", "lint"],
                    "passed_checks": ["tests", "lint"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "completed"
