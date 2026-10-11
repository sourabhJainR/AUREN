from pathlib import Path

import pytest

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


def register_protected_handler(controller):
    controller.register_handler(
        "tests.ci_gate_handler",
        ci_gate_handler,
        ci_gate_policy={
            "expected_head_sha": "trusted-head",
            "required_checks": ["tests", "lint"],
        },
    )


def test_ci_gate_policy_cannot_be_disabled_or_weakened_by_task_payload(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            # These attacker-controlled fields must have no effect.
            "require_ci_gate": False,
            "expected_head_sha": "attacker-selected-head",
            "required_checks": [],
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "attacker-selected-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["tests"],
                    "skipped_checks": ["lint"],
                },
            },
        },
    )
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "failed"


def test_reregistering_handler_without_policy_preserves_existing_gate(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    # Common service restart path: handler reference is refreshed without a new policy.
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "result": {"success": True, "detail": "pretend verified"},
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gated_task_rejects_missing_evidence_even_if_payload_disables_gate(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "result": {"success": True, "detail": "pretend verified"},
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gated_task_rejects_stale_head(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "old-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["tests", "lint"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gated_task_completes_only_for_trusted_head_and_all_required_checks(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            # Attempted overrides are ignored; trusted registration controls policy.
            "expected_head_sha": "wrong-head",
            "required_checks": ["only-one-check"],
            "result": {
                "success": True,
                "detail": "change verified",
                "ci_gate": {
                    "head_sha": "trusted-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["tests", "lint"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "completed"


def test_ci_gate_policy_requires_expected_sha_and_nonempty_checks(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    with pytest.raises(ValueError, match="expected_head_sha"):
        controller.register_handler(
            "tests.ci_gate_handler", ci_gate_handler,
            ci_gate_policy={"required_checks": ["tests"]},
        )
    with pytest.raises(ValueError, match="required_checks"):
        controller.register_handler(
            "tests.ci_gate_handler", ci_gate_handler,
            ci_gate_policy={"expected_head_sha": "trusted-head", "required_checks": []},
        )
