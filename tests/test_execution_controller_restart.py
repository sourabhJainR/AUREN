from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone
import json
import hashlib
import pytest
import os
import subprocess
import time
import sys
from threading import Event, Thread

from portable.execution_controller import ExecutionController, LeaseLostError, WorkKeyConflict
from portable.provider_operation_lifecycle import ProviderOutcomeBlocked

def test_process_death_running_task_recovers_in_fresh_process(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    seed_script = r"""
import sys
from pathlib import Path
from portable.execution_controller import ExecutionController
from tests.test_execution_controller import durable_handler
root = Path(sys.argv[1])
controller = ExecutionController(root)
controller.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
task = controller.submit("tests.test_execution_controller.durable_handler", {"id": "process-death"})
claimed = controller._claim(task.id)
assert claimed is not None and claimed.state == "running"
print(task.id)
"""
    seeded = subprocess.run(
        [sys.executable, "-c", seed_script, str(tmp_path)],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=10,
    )
    task_id = seeded.stdout.strip()
    assert task_id

    recovery_script = r"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from portable.execution_controller import ExecutionController
root = Path(sys.argv[1])
task_id = sys.argv[2]
controller = ExecutionController(root)
live_lease_results = controller.resume_pending()
assert live_lease_results == [], "a fresh process must not steal a live worker lease"
expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
with sqlite3.connect(controller.db_path) as db:
    db.execute("UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, task_id))
recovered = controller.resume_pending()
task = controller.task(task_id)
print(json.dumps({
    "live_lease_results": len(live_lease_results),
    "recovered_results": len(recovered),
    "state": task.state,
    "attempts": task.attempts,
}))
"""
    recovered = subprocess.run(
        [sys.executable, "-c", recovery_script, str(tmp_path), task_id],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=10,
    )
    result = json.loads(recovered.stdout.strip().splitlines()[-1])
    assert result == {
        "live_lease_results": 0,
        "recovered_results": 1,
        "state": "completed",
        "attempts": 2,
    }

def slow_durable_handler(payload):
    time.sleep(float(payload.get("sleep_seconds", 0.08)))
    return {"success": True, "evidence": ["slow-handler"]}


def test_supervisor_renews_lease_while_handler_runs(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller._LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.01
    controller.register_handler(
        "tests.test_execution_controller_restart.slow_durable_handler",
        slow_durable_handler,
        execution_class="read_only",
    )
    task = controller.submit(
        "tests.test_execution_controller_restart.slow_durable_handler",
        {"sleep_seconds": 0.08},
    )
    heartbeat_calls = []
    original_heartbeat = controller.heartbeat

    def count_heartbeat(claim, *, lease_seconds=300):
        heartbeat_calls.append(claim.lease_generation)
        return original_heartbeat(claim, lease_seconds=lease_seconds)

    controller.heartbeat = count_heartbeat
    score = controller.run_once(task.id)
    assert score is not None
    assert len(heartbeat_calls) >= 3
    assert controller.task(task.id).state == "completed"


def test_legacy_task_schema_adds_fencing_columns_without_losing_rows(tmp_path: Path):
    state_dir = tmp_path / ".auren"
    state_dir.mkdir(parents=True)
    db_path = state_dir / "execution.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE tasks("
            "id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL, "
            "state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, work_unit_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT, lease_until TEXT)"
        )
        db.execute(
            "INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until) "
            "VALUES('legacy-task','tests.test_execution_controller.durable_handler','{}','pending',0,'work','now','now','now','now')"
        )
    controller = ExecutionController(tmp_path)
    with sqlite3.connect(controller.db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        row = db.execute("SELECT id,state,lease_generation,lease_token FROM tasks WHERE id='legacy-task'").fetchone()
    assert {"lease_generation", "lease_token", "deadline_seconds"} <= columns
    assert row == ("legacy-task", "pending", 0, None)


def test_reclaimed_task_fences_old_worker_heartbeat_and_finalization(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "fenced-owner"})
    old_claim = first._claim(task.id)
    assert old_claim is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(first.db_path) as db:
        db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (expired, task.id))

    second = ExecutionController(tmp_path)
    new_claim = second._claim(task.id)
    assert new_claim is not None
    assert new_claim.lease_generation > old_claim.lease_generation
    assert new_claim.lease_token != old_claim.lease_token

    try:
        first.heartbeat(old_claim)
    except LeaseLostError as exc:
        assert "lease was lost" in str(exc)
    else:
        raise AssertionError("stale worker heartbeat must be rejected")
    assert not first._finalize_claim(old_claim, "completed")
    second.heartbeat(new_claim)
    assert second._finalize_claim(new_claim, "completed")
    assert second.task(task.id).state == "completed"


def test_resume_pending_task_after_controller_restart(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "restart"})
    second = ExecutionController(tmp_path)
    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"

def test_resume_waits_for_live_lease_and_reclaims_expired_running_task(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "lease-restart"})
    claimed = first._claim(task.id)
    assert claimed is not None and claimed.state == "running"

    second = ExecutionController(tmp_path)
    assert second.resume_pending() == []
    assert second.task(task.id).state == "running"

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(second.db_path) as db:
        db.execute("UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, task.id))

    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"
    assert second.task(task.id).attempts == 2


def test_recover_stale_respects_live_lease_and_only_resets_stale_task(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "stale-recovery"})
    assert first._claim(task.id) is not None

    second = ExecutionController(tmp_path)
    second.recover_stale(task.id, reason="race with live worker")
    assert second.task(task.id).state == "running"

    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with sqlite3.connect(second.db_path) as db:
        db.execute(
            "UPDATE tasks SET heartbeat_at=?, lease_until=? WHERE id=?",
            (stale, stale, task.id),
        )
    second.recover_stale(task.id, reason="confirmed stale worker")
    assert second.task(task.id).state == "pending"


def durable_handler(payload):
    return {"success": True, "evidence": [payload["id"]]}


_RESTART_HANDLER_STARTED = Event()


def pending_cancel_handler(_payload):
    raise AssertionError("cancelled task must not dispatch its handler")


def cancellation_aware_handler(payload):
    _RESTART_HANDLER_STARTED.set()
    payload["auren_cancel_event"].wait(timeout=4)
    return {"success": True, "detail": "handler observed cancellation", "evidence": ["cancel-aware"]}


def consequential_cancel_handler(payload):
    _RESTART_HANDLER_STARTED.set()
    payload["auren_cancel_event"].wait(timeout=4)
    return {"success": True, "detail": "remote action may have completed"}


def cancellation_context_provider(_payload):
    return {"trusted": True}


def cancellation_ci_provider(_payload):
    return {"status": "completed", "conclusion": "success"}


def deadline_aware_handler(payload):
    _RESTART_HANDLER_STARTED.set()
    payload["auren_cancel_event"].wait(timeout=4)
    return {"success": True, "detail": "late result"}


def deadline_validation_handler(_payload):
    return {"success": True}


def interrupted_cancel_handler(payload):
    _RESTART_HANDLER_STARTED.set()
    payload["auren_cancel_event"].wait(timeout=4)
    raise InterruptedError("subprocess provider cancelled")


def test_pending_task_cancellation_is_durable_and_never_dispatches(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = "tests.test_execution_controller_restart.pending_cancel_handler"
    controller.register_handler(name, pending_cancel_handler, execution_class="read_only")
    task = controller.submit(name, {})
    assert controller.request_cancellation(task.id, reason="operator stop")
    assert controller.request_cancellation(task.id, reason="duplicate request")
    restarted = ExecutionController(tmp_path)
    restarted.register_handler(name, pending_cancel_handler, execution_class="read_only")
    assert restarted.run_once(task.id) is None
    assert restarted.task(task.id).state == "cancelled"


def test_active_task_cancellation_reaches_handler_and_rejects_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    _RESTART_HANDLER_STARTED.clear()
    name = "tests.test_execution_controller_restart.cancellation_aware_handler"
    controller.register_handler(name, cancellation_aware_handler, execution_class="read_only")
    task = controller.submit(name, {})
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(controller.run_once(task.id)), daemon=True)
    worker.start()
    assert _RESTART_HANDLER_STARTED.wait(timeout=3), "handler did not start"
    assert controller.request_cancellation(task.id, reason="test cancellation")
    worker.join(timeout=5)
    assert not worker.is_alive(), "cancellation-aware handler did not exit"
    assert outcomes == [None]
    assert controller.task(task.id).state == "cancelled"
    with sqlite3.connect(controller.db_path) as db:
        requested = db.execute(
            "SELECT cancel_requested, cancel_reason_digest FROM tasks WHERE id=?", (task.id,),
        ).fetchone()
    assert requested[0] == 1
    assert isinstance(requested[1], str) and len(requested[1]) == 64
    assert "test cancellation" not in requested[1]


def test_consequential_cancellation_after_dispatch_is_ambiguous_not_cancelled(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    _RESTART_HANDLER_STARTED.clear()
    context_name = "tests.test_execution_controller_restart.cancellation_context_provider"
    ci_name = "tests.test_execution_controller_restart.cancellation_ci_context_provider"
    name = "tests.test_execution_controller_restart.consequential_cancel_handler"
    controller.register_handler(context_name, cancellation_context_provider, execution_class="context_provider")
    controller.register_handler(ci_name, cancellation_ci_provider, execution_class="context_provider")
    controller.register_handler(
        name, consequential_cancel_handler,
        promotion_gate_policy={"context_handler": context_name},
        ci_gate_policy={"expected_head_sha": "b" * 40, "required_checks": ["required-ci"], "evidence_handler": ci_name},
        execution_class="consequential",
    )
    submitted = controller.submit(name, {})
    claimed = controller._claim(submitted.id)
    assert claimed is not None
    errors = []
    def invoke():
        try:
            controller._invoke_with_lease_heartbeat(claimed, consequential_cancel_handler)
        except BaseException as exc:
            errors.append(exc)
    worker = Thread(target=invoke, daemon=True)
    worker.start()
    assert _RESTART_HANDLER_STARTED.wait(timeout=3), "consequential handler did not start"
    assert controller.request_cancellation(submitted.id, reason="cancel consequential operation")
    worker.join(timeout=5)
    assert not worker.is_alive(), "consequential handler did not stop cooperatively"
    assert len(errors) == 1 and isinstance(errors[0], ProviderOutcomeBlocked)
    assert controller.task(submitted.id).state == "running"


def test_active_task_deadline_is_durable_and_rejects_late_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    _RESTART_HANDLER_STARTED.clear()
    name = "tests.test_execution_controller_restart.deadline_aware_handler"
    controller.register_handler(name, deadline_aware_handler, execution_class="read_only")
    task = controller.submit(name, {}, deadline_seconds=2.0)
    assert task.deadline_at is not None
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(controller.run_once(task.id)), daemon=True)
    worker.start()
    assert _RESTART_HANDLER_STARTED.wait(timeout=3), "deadline-aware handler did not start"
    worker.join(timeout=5)
    assert not worker.is_alive(), "deadline-aware handler did not observe the deadline signal"
    assert outcomes == [None]
    assert controller.task(task.id).state == "failed"


def test_deadline_validation_and_stable_work_key_deadline_replay(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = "tests.test_execution_controller_restart.deadline_validation_handler"
    controller.register_handler(name, deadline_validation_handler, execution_class="read_only")
    for value in (0, -1, float("nan"), float("inf"), True, 86401):
        try:
            controller.submit(name, {"id": "invalid-deadline"}, deadline_seconds=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid deadline accepted: {value!r}")
    first = controller.submit(
        name, {"id": "deadline-idempotency"}, work_key="test:deadline-idempotency", deadline_seconds=30,
    )
    replay = controller.submit(
        name, {"id": "deadline-idempotency"}, work_key="test:deadline-idempotency", deadline_seconds=30.0,
    )
    assert replay.id == first.id
    assert replay.deadline_at == first.deadline_at
    with pytest.raises(WorkKeyConflict, match="deadline policy"):
        controller.submit(
            name, {"id": "deadline-idempotency"}, work_key="test:deadline-idempotency", deadline_seconds=60,
        )
    with pytest.raises(WorkKeyConflict, match="deadline policy"):
        controller.submit(name, {"id": "deadline-idempotency"}, work_key="test:deadline-idempotency")
    restarted = ExecutionController(tmp_path)
    restored = restarted.task(first.id)
    assert restored is not None and restored.deadline_at == first.deadline_at
    replayed_after_restart = restarted.submit(
        name, {"id": "deadline-idempotency"}, work_key="test:deadline-idempotency", deadline_seconds=30,
    )
    assert replayed_after_restart.id == first.id
    assert replayed_after_restart.deadline_at == first.deadline_at


def test_legacy_work_key_without_deadline_policy_remains_compatible(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = "tests.test_execution_controller_restart.deadline_validation_handler"
    controller.register_handler(name, deadline_validation_handler, execution_class="read_only")
    task = controller.submit(
        name, {"id": "legacy-deadline"}, work_key="test:legacy-deadline",
    )
    with sqlite3.connect(controller.db_path) as db:
        payload_row = db.execute("SELECT payload FROM tasks WHERE id=?", (task.id,)).fetchone()
        payload = json.loads(payload_row[0])
        legacy_intent = json.dumps(
            {"handler": name, "goal": f"execute durable handler: {name}", "payload": payload},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
        legacy_digest = hashlib.sha256(legacy_intent.encode("utf-8")).hexdigest()
        db.execute(
            "UPDATE tasks SET intent_digest=?,deadline_seconds=NULL WHERE id=?",
            (legacy_digest, task.id),
        )

    restarted = ExecutionController(tmp_path)
    restarted.register_handler(name, deadline_validation_handler, execution_class="read_only")
    replay = restarted.submit(
        name, {"id": "legacy-deadline"}, work_key="test:legacy-deadline",
    )
    assert replay.id == task.id
    assert replay.deadline_at is None
    with pytest.raises(WorkKeyConflict, match="deadline policy"):
        restarted.submit(
            name, {"id": "legacy-deadline"}, work_key="test:legacy-deadline", deadline_seconds=30,
        )


def test_handler_interruption_after_cancellation_commits_cancelled_state(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    _RESTART_HANDLER_STARTED.clear()
    name = "tests.test_execution_controller_restart.interrupted_cancel_handler"
    controller.register_handler(name, interrupted_cancel_handler, execution_class="read_only")
    task = controller.submit(name, {})
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(controller.run_once(task.id)), daemon=True)
    worker.start()
    assert _RESTART_HANDLER_STARTED.wait(timeout=3)
    assert controller.request_cancellation(task.id, reason="stop subprocess")
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert outcomes == [None]
    assert controller.task(task.id).state == "cancelled"
