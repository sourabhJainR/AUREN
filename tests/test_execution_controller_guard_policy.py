import json
import sqlite3
from pathlib import Path

from portable.execution_controller import ExecutionController
from portable.workflow_profile import WorkflowProfile


def guarded_marker_handler(payload):
    marker = Path(payload["marker_path"])
    marker.write_text("dispatched", encoding="utf-8")
    return {"success": True, "detail": "done"}


def test_guard_policy_denial_blocks_dispatch_before_handler(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.guarded_marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "schema_version": "auren-guard-v1",
        "default_effect": "allow",
        "rules": [{"id": "deny-handler", "effect": "deny", "tool": "tests.guarded_marker_handler"}],
    }), encoding="utf-8")
    marker = tmp_path / "handler-ran.txt"
    task = controller.submit("tests.guarded_marker_handler", {
        "action": "read", "marker_path": str(marker),
    })

    controller.run_once(task.id)

    assert not marker.exists()
    assert controller.task(task.id).state != "completed"
    with sqlite3.connect(controller.evidence_capture.path) as db:
        row = db.execute(
            "SELECT metadata_json FROM engineering_events WHERE task_id=? AND event_type='task_submitted'",
            (task.id,),
        ).fetchone()
    assert row is not None
    metadata = json.loads(row[0])
    assert metadata["guard_policy_digest"] != "builtin-baseline"
    assert metadata["workflow_profile_digest"] == task.payload["_auren_workflow_profile_digest"]


def test_profile_receipt_records_modes_without_business_payload(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.guarded_marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    profile = WorkflowProfile(delivery_mode="working_tree", review_mode="security_focused")
    task = controller.submit("tests.guarded_marker_handler", {
        "workflow_profile": profile.as_dict(),
        "private_business_input": "must-not-enter-receipt",
        "marker_path": str(tmp_path / "marker"),
    })
    with sqlite3.connect(controller.evidence_capture.path) as db:
        row = db.execute(
            "SELECT metadata_json FROM engineering_events WHERE task_id=? AND event_type='task_submitted'",
            (task.id,),
        ).fetchone()
    assert row is not None
    encoded = row[0]
    metadata = json.loads(encoded)
    assert metadata["workflow_profile_digest"] == profile.digest
    assert metadata["workflow_delivery_mode"] == "working_tree"
    assert metadata["workflow_review_mode"] == "security_focused"
    assert "private_business_input" not in encoded
    assert "must-not-enter-receipt" not in encoded


def test_restart_keeps_submitted_profile_when_saved_preferences_change(tmp_path: Path):
    from portable.workflow_profile import WorkflowProfile
    from portable.workflow_profile_store import save_profile

    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    saved_path = tmp_path / ".auren" / "workflow_profile.json"
    save_profile(saved_path, WorkflowProfile(delivery_mode="stage_only"))
    marker = tmp_path / "must-not-run.txt"
    task = controller.submit("tests.marker_handler", {
        "operation": "merge",
        "marker_path": str(marker),
    })
    submitted_digest = task.payload["_auren_workflow_profile_digest"]
    assert task.payload["_auren_workflow_profile"]["delivery_mode"] == "stage_only"

    # A changed saved profile must not retroactively widen the submitted task.
    save_profile(saved_path, WorkflowProfile(delivery_mode="pull_request"))
    restarted = ExecutionController(tmp_path)
    restarted.register_handler(
        "tests.marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    recovered = restarted.task(task.id)
    assert recovered.payload["_auren_workflow_profile_digest"] == submitted_digest
    assert recovered.payload["_auren_workflow_profile"]["delivery_mode"] == "stage_only"

    restarted.run_once(task.id)
    assert not marker.exists()
    assert restarted.task(task.id).state != "completed"


def test_invalid_configured_guard_policy_is_durable_and_blocks_dispatch(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.guarded_marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        '{"schema_version":"auren-guard-v1","schema_version":"auren-guard-v1","rules":[]}',
        encoding="utf-8",
    )
    marker = tmp_path / "must-not-run.txt"
    task = controller.submit("tests.guarded_marker_handler", {"marker_path": str(marker)})
    with sqlite3.connect(controller.evidence_capture.path) as db:
        row = db.execute(
            "SELECT metadata_json FROM engineering_events WHERE task_id=? AND event_type='task_submitted'",
            (task.id,),
        ).fetchone()
    assert row is not None
    assert json.loads(row[0])["guard_policy_status"] == "invalid"

    controller.run_once(task.id)
    assert not marker.exists()
    assert controller.task(task.id).state != "completed"


def test_guard_action_comes_from_trusted_registration_not_task_payload(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.generic_handler", guarded_marker_handler,
        execution_class="read_only", guard_action="read",
    )
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "schema_version": "auren-guard-v1",
        "default_effect": "allow",
        "rules": [{"id": "deny-reads", "effect": "deny", "action": "read"}],
    }), encoding="utf-8")
    marker = tmp_path / "handler-ran.txt"
    task = controller.submit("tests.generic_handler", {
        "action": "unrelated-payload-label",
        "marker_path": str(marker),
    })

    controller.run_once(task.id)

    assert not marker.exists()
    assert controller.task(task.id).state != "completed"


def test_policy_tightening_between_submit_and_dispatch_is_enforced(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.mutable_policy_handler", guarded_marker_handler,
        execution_class="read_only", guard_action="read",
    )
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "schema_version": "auren-guard-v1",
        "default_effect": "allow",
        "rules": [],
    }), encoding="utf-8")
    marker = tmp_path / "must-not-run.txt"
    task = controller.submit("tests.mutable_policy_handler", {"marker_path": str(marker)})

    # Policy is changed after durable submission but before any handler dispatch.
    policy_path.write_text(json.dumps({
        "schema_version": "auren-guard-v1",
        "default_effect": "allow",
        "rules": [{"id": "deny-read", "effect": "deny", "action": "read"}],
    }), encoding="utf-8")
    controller.run_once(task.id)

    assert not marker.exists()
    assert controller.task(task.id).state != "completed"


def test_consequential_ledger_binding_contains_guard_action_and_policy_digest(tmp_path: Path):
    from types import SimpleNamespace
    from unittest.mock import patch

    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.guarded_marker_handler", guarded_marker_handler,
        execution_class="read_only", guard_action="publish",
    )
    task = controller.submit("tests.guarded_marker_handler", {
        "marker_path": str(tmp_path / "must-not-run.txt"),
    })
    with patch(
        "portable.execution_controller.DurableSideEffectLedger.begin",
        return_value=SimpleNamespace(
            action="replay", state="completed",
            receipt={"result": {"success": True, "detail": "previous receipt"}},
        ),
    ) as begin:
        result = controller._invoke_consequential_with_ledger(task, guarded_marker_handler)

    binding = begin.call_args.args[1]
    assert binding["guard"]["action"] == "publish"
    assert binding["guard"]["effect"] == "allow"
    assert len(binding["guard"]["policy_digest"]) == 64
    assert result["detail"] == "previous receipt"
    assert not (tmp_path / "must-not-run.txt").exists()


def test_guard_policy_change_is_rechecked_at_provider_dispatch(tmp_path: Path):
    import pytest
    from unittest.mock import patch

    from portable.organization_guard import evaluate_guard_policy

    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.guarded_marker_handler", guarded_marker_handler,
        execution_class="read_only", guard_action="publish",
    )
    task = controller.submit("tests.guarded_marker_handler", {
        "marker_path": str(tmp_path / "must-not-run.txt"),
    })
    context = {"action": "publish", "tool": "tests.guarded_marker_handler"}
    allow_policy = {
        "schema_version": "auren-guard-v1", "default_effect": "allow", "rules": [],
    }
    deny_policy = {
        "schema_version": "auren-guard-v1", "default_effect": "allow",
        "rules": [{"id": "deny-publish", "effect": "deny", "action": "publish"}],
    }
    allow = evaluate_guard_policy(allow_policy, context)
    deny = evaluate_guard_policy(deny_policy, context)

    def dispatch_without_remote_call(_self, _key, _provider, intent, dispatch):
        return dispatch(intent, "operation-id", "idempotency-key")

    with (
        patch.object(controller, "_evaluate_guard_for_task", side_effect=[allow, deny]),
        patch(
            "portable.execution_controller.DurableSideEffectLedger.begin",
            return_value=SimpleNamespace(action="execute", state="pending", receipt=None),
        ),
        patch(
            "portable.provider_operation_lifecycle.ProviderOperationLifecycle.execute",
            autospec=True,
            side_effect=dispatch_without_remote_call,
        ),
    ):
        with pytest.raises(RuntimeError, match="Guard policy changed or denied"):
            controller._invoke_consequential_with_ledger(task, guarded_marker_handler)

    assert not (tmp_path / "must-not-run.txt").exists()
