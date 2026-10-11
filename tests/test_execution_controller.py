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
    # Deliberately return task-controlled data to prove it is not trusted as CI evidence.
    return payload["result"]


def trusted_ci_success(_policy):
    return {
        "head_sha": "trusted-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests", "lint"],
        "skipped_checks": [],
    }


def trusted_ci_stale(_policy):
    return {
        "head_sha": "old-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests", "lint"],
        "skipped_checks": [],
    }


def trusted_ci_skipped(_policy):
    return {
        "head_sha": "trusted-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests"],
        "skipped_checks": ["lint"],
    }


def trusted_ci_missing(_policy):
    return None


def register_protected_handler(controller, provider=trusted_ci_success):
    controller.register_handler("tests.ci_provider", provider)
    controller.register_handler(
        "tests.ci_gate_handler",
        ci_gate_handler,
        ci_gate_policy={
            "expected_head_sha": "trusted-head",
            "required_checks": ["tests", "lint"],
            "evidence_handler": "tests.ci_provider",
        },
    )


def test_task_payload_cannot_disable_gate_or_forge_ci_evidence(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_stale)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "expected_head_sha": "attacker-selected-head",
            "required_checks": [],
            "result": {
                "success": True,
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
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "failed"


def test_missing_trusted_ci_evidence_fails_even_if_task_claims_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_missing)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "result": {
                "success": True,
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
    assert controller.task(task.id).state == "failed"


def test_trusted_ci_provider_rejects_skipped_required_check(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_skipped)
    task = controller.submit("tests.ci_gate_handler", {"result": {"success": True}})
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gate_uses_trusted_sha_and_required_checks_not_task_overrides(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "expected_head_sha": "wrong-head",
            "required_checks": ["only-one-check"],
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "wrong-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["only-one-check"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "completed"


def test_reregistering_handler_without_policy_preserves_existing_gate(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_stale)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler)
    task = controller.submit(
        "tests.ci_gate_handler",
        {"require_ci_gate": False, "result": {"success": True}},
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gate_policy_requires_expected_sha_checks_and_provider(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.ci_provider", trusted_ci_success)

    invalid_policies = [
        (
            {"required_checks": ["tests"], "evidence_handler": "tests.ci_provider"},
            "expected_head_sha",
        ),
        (
            {
                "expected_head_sha": "trusted-head",
                "required_checks": [],
                "evidence_handler": "tests.ci_provider",
            },
            "required_checks",
        ),
        (
            {"expected_head_sha": "trusted-head", "required_checks": ["tests"]},
            "evidence_handler",
        ),
    ]
    for policy, expected_message in invalid_policies:
        try:
            controller.register_handler(
                "tests.ci_gate_handler", ci_gate_handler, ci_gate_policy=policy,
            )
        except ValueError as error:
            assert expected_message in str(error)
        else:
            raise AssertionError(f"invalid CI policy should reject {expected_message}")
