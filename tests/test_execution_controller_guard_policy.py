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
        "rules": [{"id": "deny-read", "effect": "deny", "action": "read"}],
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
        "tests.merge_marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    saved_path = tmp_path / ".auren" / "workflow_profile.json"
    save_profile(saved_path, WorkflowProfile(delivery_mode="stage_only"))
    marker = tmp_path / "must-not-run.txt"
    task = controller.submit("tests.merge_marker_handler", {
        "marker_path": str(marker),
    })
    submitted_digest = task.payload["_auren_workflow_profile_digest"]
    assert task.payload["_auren_workflow_profile"]["delivery_mode"] == "stage_only"

    # A changed saved profile must not retroactively widen the submitted task.
    save_profile(saved_path, WorkflowProfile(delivery_mode="pull_request"))
    restarted = ExecutionController(tmp_path)
    restarted.register_handler(
        "tests.merge_marker_handler", guarded_marker_handler, execution_class="read_only"
    )
    recovered = restarted.task(task.id)
    assert recovered.payload["_auren_workflow_profile_digest"] == submitted_digest
    assert recovered.payload["_auren_workflow_profile"]["delivery_mode"] == "stage_only"

    restarted.run_once(task.id)
    assert not marker.exists()
    assert restarted.task(task.id).state != "completed"
