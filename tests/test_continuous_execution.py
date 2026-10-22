from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
import time
from urllib.error import HTTPError
from unittest.mock import patch, Mock

from portable.continuous_execution import ContinuousExecutionSupervisor, GitHubActionsCheckPoller, discover_git_target
from portable.execution_controller import ExecutionController


def durable_continuous_handler(payload):
    return {"success": True, "detail": "processed", "evidence": [payload["work_key"]]}


def durable_repair_handler(payload):
    return {"success": True, "stable_key": payload["auren_ci_repair"]["stable_key"],
            "evidence_refs": payload["evidence_refs"]}


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload[:size]


def test_continuous_loop_processes_tasks_and_keeps_hash_chained_audit(tmp_path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.continuous", durable_continuous_handler, execution_class="read_only")
    task = controller.submit("tests.continuous", {"work_key": "one"})
    supervisor = ContinuousExecutionSupervisor(controller)

    report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=1)

    assert controller.task(task.id).state == "completed"
    assert report["state"] == "stopped"
    assert report["audit_chain_valid"] is True
    assert supervisor.verify_audit_chain() is True
    status = supervisor.status()
    assert status["task_counts"]["completed"] == 1
    assert status["recent_terminal_tasks"][0]["task_id"] == task.id
    assert any(event["event_type"] == "cycle_completed" for event in status["recent_events"])


def test_stop_command_is_durable_and_worker_exits_without_losing_pending_work(tmp_path):
    controller = ExecutionController(tmp_path)
    supervisor = ContinuousExecutionSupervisor(controller)
    result = {}

    worker = threading.Thread(
        target=lambda: result.update(supervisor.run(poll_seconds=0.1, batch_size=1)),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 3
    while supervisor.status()["state"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_status = ContinuousExecutionSupervisor(ExecutionController(tmp_path)).request_stop("test_interrupt")
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert stop_status["state"] in {"stop_requested", "stopped"}
    assert result["state"] == "stopped"
    assert result["stop_reason"] == "test_interrupt"
    assert supervisor.verify_audit_chain() is True


def test_second_live_supervisor_cannot_claim_same_project(tmp_path):
    first = ContinuousExecutionSupervisor(ExecutionController(tmp_path), lease_seconds=5)
    second = ContinuousExecutionSupervisor(ExecutionController(tmp_path), lease_seconds=5)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(first.run(poll_seconds=0.1, max_cycles=10_000)),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 3
    while first.status()["state"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        try:
            second.run(poll_seconds=0.1, max_cycles=1)
        except RuntimeError as exc:
            assert "live lease" in str(exc) or "live AUREN supervisor" in str(exc)
        else:
            raise AssertionError("second worker unexpectedly acquired the live lease")
    finally:
        second.request_stop("test_cleanup")
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert first.verify_audit_chain()


def test_github_poller_requires_every_exact_named_check_to_succeed():
    poller = GitHubActionsCheckPoller(
        "owner/repo", "a" * 40, ["validate", "unit-tests (3.12)"], token="dummy",
    )
    payload = {"check_runs": [
        {"name": "validate", "status": "completed", "conclusion": "success"},
        {"name": "unit-tests (3.12)", "status": "completed", "conclusion": "skipped"},
    ]}
    with patch("urllib.request.urlopen", return_value=_Response(payload)):
        result = poller()
    assert result["status"] == "failure"
    assert result["failed_checks"] == ["unit-tests (3.12)"]


def test_github_poller_waits_for_missing_checks_and_never_guesses_success():
    poller = GitHubActionsCheckPoller("owner/repo", "b" * 40, ["required-check"])
    with patch("urllib.request.urlopen", return_value=_Response({"check_runs": []})):
        result = poller()
    assert result["status"] == "waiting_for_required_checks"
    assert result["missing_checks"] == ["required-check"]


def test_github_rate_limit_is_audited_as_poll_error_not_success():
    poller = GitHubActionsCheckPoller("owner/repo", "c" * 40, ["required-check"])
    rate_limit = HTTPError("https://api.github.com", 429, "rate limited", {}, None)
    with patch("urllib.request.urlopen", side_effect=rate_limit):
        result = poller()
    assert result["status"] == "poll_error"
    assert result["error_type"] == "HTTPError"
    assert result["head_sha"] == "c" * 40


def test_stale_ci_poller_is_visible_and_audited_on_next_cycle(tmp_path):
    controller = ExecutionController(tmp_path)
    poller_name = "ci:owner/repo"
    supervisor = ContinuousExecutionSupervisor(
        controller, pollers={poller_name: lambda: {"status": "success", "repository": "owner/repo", "head_sha": "a" * 40}},
    )
    stale_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with patch("portable.continuous_execution._now", return_value=stale_timestamp):
        supervisor._event("previous-run", "poll_result", {
            "poller": poller_name, "result": {"status": "success"},
        })
    health = supervisor.status()["poller_health"][0]
    assert health["poller"] == poller_name
    assert health["last_poll_at"] == stale_timestamp
    assert health["last_status"] == "success"
    assert health["age_seconds"] >= 300
    assert health["stale"] is True

    report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=1)
    status = supervisor.status(event_limit=100)
    assert report["audit_chain_valid"] is True
    assert any(event["event_type"] == "stale_ci_polling_detected" for event in status["recent_events"])
    assert status["poller_health"][0]["stale"] is False


def test_audit_chain_detects_tampering(tmp_path):
    supervisor = ContinuousExecutionSupervisor(ExecutionController(tmp_path))
    supervisor.request_stop("audit test")
    assert supervisor.verify_audit_chain()
    import sqlite3
    with sqlite3.connect(supervisor.path) as db:
        db.execute("UPDATE events SET payload_json='{}' WHERE sequence=1")
    assert not supervisor.verify_audit_chain()



def test_failed_required_check_enqueues_one_bounded_repair_through_existing_supervisor(tmp_path):
    from portable.engineering_recovery import EngineeringRecoveryLedger

    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.repair_ci", durable_repair_handler, execution_class="read_only")
    head = "e" * 40
    result = {
        "status": "failure", "repository": "owner/repo", "head_sha": head,
        "failed_checks": ["hardening"],
        "checks": {"hardening": {"status": "completed", "conclusion": "failure", "id": 17,
                                  "evidence_url": "https://github.com/owner/repo/actions/runs/17"}},
    }
    supervisor = ContinuousExecutionSupervisor(
        controller, pollers={"ci:owner/repo": lambda: result},
        repair_handler="tests.repair_ci",
        recovery_ledger=EngineeringRecoveryLedger(tmp_path / ".auren" / "engineering-recovery.sqlite3"),
    )
    report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=2)
    status = supervisor.status(event_limit=100)
    assert report["audit_chain_valid"] is True
    assert any(event["event_type"] == "ci_repair_queued" for event in status["recent_events"])
    assert any(event["event_type"] == "ci_repair_finalized" for event in status["recent_events"])
    lifecycle = [
        event["payload"]["to_state"]
        for event in reversed(status["recent_events"])
        if event["event_type"] == "ci_repair_lifecycle"
    ]
    assert lifecycle == ["observed", "triaged", "queued", "claimed", "repairing", "blocked"]
    assert "resolved" not in lifecycle  # completed task is not success without new validation evidence
    with __import__("sqlite3").connect(controller.db_path) as db:
        rows = db.execute("SELECT payload FROM tasks").fetchall()
    repairs = [json.loads(row[0]) for row in rows if "auren_ci_repair" in json.loads(row[0])]
    assert len(repairs) == 1
    assert repairs[0]["auren_ci_repair"]["head_sha"] == head


def test_github_poller_observation_without_configured_required_checks_is_not_a_pass():
    poller = GitHubActionsCheckPoller("owner/repo", "c" * 40, [])
    with patch("urllib.request.urlopen", return_value=_Response({
        "check_runs": [
            {"id": 1, "name": "validate", "status": "completed", "conclusion": "success"},
        ]
    })):
        result = poller()
    assert result["status"] == "observed_complete_unverified"
    assert result["required_checks"] == []


def test_discover_git_target_uses_current_head_and_github_origin(tmp_path):
    remote = Mock(stdout="git@github.com:sourabhJainR/AUREN.git" + chr(10))
    head = Mock(stdout="d" * 40 + chr(10))
    with patch("subprocess.run", side_effect=[remote, head]) as run:
        assert discover_git_target(tmp_path) == ("sourabhJainR/AUREN", "d" * 40)
    assert run.call_count == 2
    assert all(call.kwargs["timeout"] == 3 for call in run.call_args_list)
