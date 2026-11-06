from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
import time
from urllib.error import HTTPError
from unittest.mock import patch, Mock

from portable.continuous_execution import ContinuousExecutionSupervisor, GitHubActionsCheckPoller, GitHubPostMergeVerifier, discover_git_target
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


def test_blocked_goal_waits_for_explicit_wake_and_does_not_spin(tmp_path):
    calls = []
    controller = ExecutionController(tmp_path)

    def reconcile():
        calls.append("called")
        if len(calls) == 1:
            return {"status": "waiting_for_approval", "blocker": "release approval is pending"}
        return {"status": "in_progress", "next_action": "continue_after_approval"}

    supervisor = ContinuousExecutionSupervisor(
        controller, goal_reconcilers={"AUREN:test:approval-goal": reconcile},
    )
    supervisor.run(poll_seconds=0.1, batch_size=1, max_cycles=2)
    assert calls == ["called"]
    # Simulate interruption after the hold write but before retry-state reset.
    with __import__("sqlite3").connect(supervisor.path) as db:
        db.execute("DELETE FROM goal_backoff WHERE goal=?", ("AUREN:test:approval-goal",))
    health = supervisor.status()["goal_health"]
    assert health[0]["status"] == "held"
    assert health[0]["hold"] == {
        "status": "waiting_for_approval", "reason": "release approval is pending",
    }

    replacement = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:approval-goal": reconcile},
        operator_authorizer=lambda _reason: "operator:test",
    )
    replacement.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == ["called"]
    assert any(
        event["event_type"] == "goal_reconciliation_hold_wait"
        for event in replacement.status(event_limit=100)["recent_events"]
    )

    replacement.wake_goal(
        "AUREN:test:approval-goal", "independent approval evidence was recorded",
        evidence_ref="approval:release-approval-123",
    )
    replacement.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == ["called", "called"]
    health = replacement.status()["goal_health"]
    assert health[0]["status"] == "ready"
    assert health[0]["hold"] is None
    events = replacement.status(event_limit=100)["recent_events"]
    event_types = {event["event_type"] for event in events}
    assert "goal_reconciliation_wake_authorized" in event_types
    assert "goal_reconciliation_woken" in event_types
    assert replacement.verify_audit_chain()


def test_held_goal_cannot_be_woken_without_authenticated_operator_or_evidence(tmp_path):
    controller = ExecutionController(tmp_path)
    supervisor = ContinuousExecutionSupervisor(
        controller,
        goal_reconcilers={"AUREN:test:approval-goal": lambda: {
            "status": "waiting_for_approval", "blocker": "approval required",
        }},
    )
    supervisor.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    with __import__("pytest").raises(PermissionError, match="operator authentication"):
        supervisor.wake_goal(
            "AUREN:test:approval-goal", "approval was granted",
            evidence_ref="approval:release-123",
        )

    authenticated = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:approval-goal": lambda: {"status": "in_progress"}},
        operator_authorizer=lambda _reason: "operator:test",
    )
    with __import__("pytest").raises(ValueError, match="evidence_ref"):
        authenticated.wake_goal(
            "AUREN:test:approval-goal", "approval was granted", evidence_ref="",
        )
    assert authenticated.status()["goal_health"][0]["status"] == "held"


def test_goal_reconciler_backoff_survives_restart_and_resets_after_recovery(tmp_path):
    controller = ExecutionController(tmp_path)
    calls = []

    def fail_goal():
        calls.append("failed")
        raise OSError("temporary plan store outage")

    first = ContinuousExecutionSupervisor(
        controller, goal_reconcilers={"AUREN:test:retry-goal": fail_goal},
    )
    first.run(poll_seconds=2, batch_size=1, max_cycles=1)
    with __import__("sqlite3").connect(first.path) as db:
        failures, next_retry, error_type = db.execute(
            "SELECT consecutive_errors,next_retry_at,last_error_type FROM goal_backoff WHERE goal=?",
            ("AUREN:test:retry-goal",),
        ).fetchone()
    assert failures == 1
    assert next_retry > __import__("time").time()
    assert error_type == "OSError"
    first_health = first.status()["goal_health"]
    assert first_health[0]["goal"] == "AUREN:test:retry-goal"
    assert first_health[0]["status"] == "backoff"
    assert first_health[0]["consecutive_errors"] == 1

    resumed_calls = []

    def recover_goal():
        resumed_calls.append("recovered")
        return {"status": "in_progress", "next_action": "dispatch_next_ready_task"}

    resumed = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:retry-goal": recover_goal},
    )
    resumed.run(poll_seconds=2, batch_size=1, max_cycles=1)
    assert resumed_calls == []
    assert any(
        event["event_type"] == "goal_reconciliation_backoff_wait"
        for event in resumed.status(event_limit=100)["recent_events"]
    )

    with __import__("sqlite3").connect(resumed.path) as db:
        db.execute(
            "UPDATE goal_backoff SET next_retry_at=0 WHERE goal=?",
            ("AUREN:test:retry-goal",),
        )
    resumed.run(poll_seconds=2, batch_size=1, max_cycles=1)
    assert resumed_calls == ["recovered"]
    with __import__("sqlite3").connect(resumed.path) as db:
        state = db.execute(
            "SELECT consecutive_errors,next_retry_at,last_error_type FROM goal_backoff WHERE goal=?",
            ("AUREN:test:retry-goal",),
        ).fetchone()
    assert state == (0, 0.0, None)
    assert resumed.verify_audit_chain()


def test_failed_goal_outcome_uses_persisted_backoff(tmp_path):
    calls = []

    def reconcile():
        calls.append("called")
        return {"status": "failed", "reason": "provider outcome was not verified"}

    supervisor = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:failed-outcome": reconcile},
        max_goal_retries=3,
    )
    supervisor.run(poll_seconds=2, batch_size=1, max_cycles=1)
    health = supervisor.status()["goal_health"]
    assert calls == ["called"]
    assert health[0]["status"] == "backoff"
    assert health[0]["consecutive_errors"] == 1
    assert health[0]["last_error_type"] == "RuntimeError"

    replacement = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:failed-outcome": reconcile},
        max_goal_retries=3,
    )
    replacement.run(poll_seconds=2, batch_size=1, max_cycles=1)
    assert calls == ["called"]
    assert any(
        event["event_type"] == "goal_reconciliation_backoff_wait"
        for event in replacement.status(event_limit=100)["recent_events"]
    )


def test_goal_retry_budget_exhaustion_holds_until_authenticated_evidence_wake(tmp_path):
    calls = []
    controller = ExecutionController(tmp_path)

    def fail_goal():
        calls.append("failed")
        raise OSError("plan store unavailable")

    first = ContinuousExecutionSupervisor(
        controller,
        goal_reconcilers={"AUREN:test:bounded-goal": fail_goal},
        max_goal_retries=2,
    )
    first.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    with __import__("sqlite3").connect(first.path) as db:
        db.execute(
            "UPDATE goal_backoff SET next_retry_at=0 WHERE goal=?",
            ("AUREN:test:bounded-goal",),
        )

    second = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:bounded-goal": fail_goal},
        max_goal_retries=2,
    )
    second.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == ["failed", "failed"]
    assert second.status()["goal_health"][0]["status"] == "held"

    second.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == ["failed", "failed"]
    assert any(
        event["event_type"] == "goal_reconciliation_hold_wait"
        for event in second.status(event_limit=100)["recent_events"]
    )

    recovered_calls = []

    def recover_goal():
        recovered_calls.append("recovered")
        return {"status": "in_progress", "next_action": "continue_with_new_evidence"}

    resumed = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path),
        goal_reconcilers={"AUREN:test:bounded-goal": recover_goal},
        operator_authorizer=lambda _reason: "operator:test",
    )
    resumed.wake_goal(
        "AUREN:test:bounded-goal",
        "new plan-store recovery evidence is available",
        evidence_ref="evidence:plan-store-recovery-456",
    )
    resumed.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert recovered_calls == ["recovered"]
    assert resumed.status()["goal_health"][0]["status"] == "ready"


def test_goal_reconciler_dispatches_followup_work_without_another_prompt(tmp_path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.goal_continuation", durable_continuous_handler, execution_class="read_only")
    invocations = []
    task_ids = []

    def reconcile_goal():
        invocations.append(len(invocations) + 1)
        if not task_ids:
            task = controller.submit(
                "tests.goal_continuation", {"work_key": "goal-unit-1"},
                goal="complete the parent engineering goal", work_key="AUREN:test:goal-unit-1",
            )
            task_ids.append(task.id)
        return {"status": "in_progress", "next_action": "reconcile_remaining_acceptance"}

    supervisor = ContinuousExecutionSupervisor(
        controller, goal_reconcilers={"AUREN:test:parent-goal": reconcile_goal},
    )
    report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=2)

    assert len(invocations) == 2
    assert controller.task(task_ids[0]).state == "completed"
    assert report["audit_chain_valid"] is True
    events = supervisor.status(event_limit=100)["recent_events"]
    assert any(
        event["event_type"] == "goal_reconciled"
        and event["payload"]["goal"] == "AUREN:test:parent-goal"
        for event in events
    )


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


def test_expired_supervisor_lease_does_not_clear_durable_stop_without_operator_action(tmp_path):
    controller = ExecutionController(tmp_path)
    first = ContinuousExecutionSupervisor(controller, lease_seconds=5)
    run_id, _ = first._claim("stop-recovery-run")
    first.request_stop("operator requested stop")

    with __import__("sqlite3").connect(first.path) as db:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute("UPDATE control SET lease_until=? WHERE id=1", (expired,))

    replacement = ContinuousExecutionSupervisor(ExecutionController(tmp_path), lease_seconds=5)
    try:
        replacement.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    except RuntimeError as exc:
        assert "explicit operator clear" in str(exc)
    else:
        raise AssertionError("replacement supervisor must not discard a durable stop")

    assert replacement.status()["state"] == "stop_requested"
    try:
        replacement.clear_stop("unauthenticated attempt")
    except PermissionError as exc:
        assert "operator authentication is not configured" in str(exc)
    else:
        raise AssertionError("stop clear must fail closed without host operator authentication")

    replacement = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path), lease_seconds=5,
        operator_authorizer=lambda _reason: "operator:verified-test-principal",
    )
    cleared = replacement.clear_stop("operator confirmed restart")
    assert cleared["state"] == "stopped"
    report = replacement.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert report["run_id"] != run_id
    events = replacement.status(event_limit=100)["recent_events"]
    assert any(event["event_type"] == "stop_cleared" for event in events)
    assert replacement.verify_audit_chain()


def test_poller_error_backoff_persists_across_restart_and_resets_on_success(tmp_path):
    calls = []
    controller = ExecutionController(tmp_path)

    def poller():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return {"status": "poll_error", "error_type": "TimeoutError"}
        return {"status": "success", "head_sha": "f" * 40}

    first = ContinuousExecutionSupervisor(controller, pollers={"ci:owner/repo": poller})
    first.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == [1]
    assert first._poller_retry_after("ci:owner/repo") > 0

    resumed = ContinuousExecutionSupervisor(
        ExecutionController(tmp_path), pollers={"ci:owner/repo": poller},
    )
    report = resumed.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == [1]
    assert any(
        event["event_type"] == "poller_backoff_wait"
        for event in resumed.status(event_limit=100)["recent_events"]
    )

    with __import__("sqlite3").connect(resumed.path) as db:
        db.execute(
            "UPDATE poller_backoff SET next_poll_at=0 WHERE poller=?",
            ("ci:owner/repo",),
        )
    resumed.run(poll_seconds=0.1, batch_size=1, max_cycles=1)
    assert calls == [1, 2]
    with __import__("sqlite3").connect(resumed.path) as db:
        state = db.execute(
            "SELECT consecutive_errors,next_poll_at,last_error_type FROM poller_backoff WHERE poller=?",
            ("ci:owner/repo",),
        ).fetchone()
    assert state == (0, 0.0, None)
    assert resumed.verify_audit_chain()


def test_supervisor_reuses_run_identity_after_expired_process_lease(tmp_path):
    controller = ExecutionController(tmp_path)
    first = ContinuousExecutionSupervisor(controller, lease_seconds=5)
    original_run_id, _ = first._claim("durable-run-identity")
    first._heartbeat(original_run_id, 7)

    with __import__("sqlite3").connect(first.path) as db:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute(
            "UPDATE control SET lease_until=?,cycle_count=7 WHERE id=1",
            (expired,),
        )

    resumed_controller = ExecutionController(tmp_path)
    resumed = ContinuousExecutionSupervisor(resumed_controller, lease_seconds=5)
    report = resumed.run(poll_seconds=0.1, batch_size=1, max_cycles=1)

    assert report["run_id"] == original_run_id
    assert report["cycles_this_run"] == 1
    assert report["cycle_count"] == 8
    events = resumed.status(event_limit=100)["recent_events"]
    started = [event for event in events if event["event_type"] == "supervisor_started"]
    assert started[-1]["payload"]["resumed_after_lease_expiry"] is True
    assert started[-1]["payload"]["starting_cycle"] == 7
    assert resumed.verify_audit_chain()


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
    with patch("portable.continuous_execution.guarded_https_get", return_value=(200, json.dumps(payload).encode("utf-8"))):
        result = poller()
    assert result["status"] == "failure"
    assert result["failed_checks"] == ["unit-tests (3.12)"]


def test_github_poller_waits_for_missing_checks_and_never_guesses_success():
    poller = GitHubActionsCheckPoller("owner/repo", "b" * 40, ["required-check"])
    with patch("portable.continuous_execution.guarded_https_get", return_value=(200, json.dumps({"check_runs": []}).encode("utf-8"))):
        result = poller()
    assert result["status"] == "waiting_for_required_checks"
    assert result["missing_checks"] == ["required-check"]


def test_github_rate_limit_is_audited_as_poll_error_not_success():
    poller = GitHubActionsCheckPoller("owner/repo", "c" * 40, ["required-check"])
    rate_limit = HTTPError("https://api.github.com", 429, "rate limited", {}, None)
    with patch("portable.continuous_execution.guarded_https_get", return_value=(429, b"rate limited")):
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



def test_replayed_ci_repair_progress_never_regresses_or_duplicates_lifecycle(tmp_path):
    supervisor = ContinuousExecutionSupervisor(ExecutionController(tmp_path))
    stable_key = "ci-repair-monotonic-test"
    states = ["observed", "triaged", "queued", "claimed", "repairing", "validating"]
    for state in states:
        supervisor._repair_transition("run-1", stable_key=stable_key, to_state=state)
    # Replaying older durable progress on later supervisor cycles must be a no-op.
    for state in ["queued", "claimed", "repairing", "validating", "observed", "triaged"]:
        supervisor._repair_transition("run-2", stable_key=stable_key, to_state=state)
    with __import__("sqlite3").connect(supervisor.path) as db:
        recorded = [
            json.loads(row[0])["to_state"]
            for row in db.execute(
                "SELECT payload_json FROM events WHERE event_type='ci_repair_lifecycle' ORDER BY sequence"
            ).fetchall()
        ]
    assert recorded == states
    assert supervisor.verify_audit_chain()


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
    with patch("portable.continuous_execution.guarded_https_get", return_value=(200, json.dumps({
        "check_runs": [
            {"id": 1, "name": "validate", "status": "completed", "conclusion": "success"},
        ]
    }).encode("utf-8"))):
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



def test_github_poller_finds_required_check_after_first_page():
    poller = GitHubActionsCheckPoller("owner/repo", "d" * 40, ["required-check"])
    first_page = {"check_runs": [
        {"id": index + 1, "name": f"unrelated-{index}", "status": "completed", "conclusion": "success"}
        for index in range(100)
    ]}
    second_page = {"check_runs": [
        {"id": 101, "name": "required-check", "status": "completed", "conclusion": "success"}
    ]}
    responses = [
        (200, json.dumps(first_page).encode("utf-8")),
        (200, json.dumps(second_page).encode("utf-8")),
    ]
    with patch("portable.continuous_execution.guarded_https_get", side_effect=responses) as request:
        result = poller()
    assert result["status"] == "success"
    assert request.call_count == 2
    assert "page=1" in request.call_args_list[0].args[0]
    assert "page=2" in request.call_args_list[1].args[0]


def test_github_poller_fails_closed_when_check_run_pagination_is_truncated():
    poller = GitHubActionsCheckPoller("owner/repo", "e" * 40, ["required-check"])
    full_page = {"check_runs": [
        {"id": index + 1, "name": f"check-{index}", "status": "completed", "conclusion": "success"}
        for index in range(100)
    ]}
    response = (200, json.dumps(full_page).encode("utf-8"))
    with patch("portable.continuous_execution.guarded_https_get", return_value=response) as request:
        result = poller()
    assert result["status"] == "poll_error"
    assert result["error_type"] == "pagination_limit"
    assert request.call_count == 10


def test_post_merge_verifier_waits_until_main_points_to_expected_merge_sha():
    verifier = GitHubPostMergeVerifier("owner/repo", "a" * 40, ["post-merge-check"])
    ref = {"object": {"sha": "b" * 40}}
    with patch("portable.continuous_execution.guarded_https_get", return_value=(200, json.dumps(ref).encode("utf-8"))) as request:
        result = verifier()
    assert result["status"] == "waiting_for_main_head"
    assert result["merge_sha"] == "a" * 40
    assert result["main_head_sha"] == "b" * 40
    assert request.call_count == 1


def test_post_merge_verifier_requires_successful_checks_on_exact_main_merge_sha():
    merge_sha = "c" * 40
    verifier = GitHubPostMergeVerifier("owner/repo", merge_sha, ["post-merge-check"])
    ref = {"object": {"sha": merge_sha}}
    checks = {"check_runs": [
        {"id": 7, "name": "post-merge-check", "status": "completed", "conclusion": "success"}
    ]}
    responses = [
        (200, json.dumps(ref).encode("utf-8")),
        (200, json.dumps(checks).encode("utf-8")),
    ]
    with patch("portable.continuous_execution.guarded_https_get", side_effect=responses) as request:
        result = verifier()
    assert result["status"] == "success"
    assert result["verification"] == "post_merge"
    assert result["merge_sha"] == merge_sha
    assert result["main_head_sha"] == merge_sha
    assert request.call_count == 2
    assert merge_sha in request.call_args_list[1].args[0]
