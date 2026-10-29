"""Tests for the observation-only AUREN engineering dashboard."""
from pathlib import Path
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from portable.engineering_dashboard import EngineeringDashboard

def test_dashboard_snapshot_uses_repository_and_test_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); (root/"tests").mkdir(); (root/"portable").mkdir()
        (root/"portable"/"sample.py").write_text("def work():\n    return 1\n",encoding="utf-8")
        (root/"tests"/"test_sample.py").write_text("def test_work():\n    assert True\n",encoding="utf-8")
        dashboard=EngineeringDashboard(root)
        dashboard.record_event(run_id="r1",event="task-start",task_id="t1",status="running",duration_ms=120)
        dashboard.record_event(run_id="r1",event="task-complete",task_id="t1",status="completed",duration_ms=480)
        snapshot=dashboard.snapshot().as_dict()
        assert snapshot["repository"]["files"]>=2
        assert snapshot["tests"]["files"]==1
        assert snapshot["tests"]["cases"]==1
        assert snapshot["usage"]["runs"]==1
        assert snapshot["usage"]["average_task_duration_ms"]==300
        assert snapshot["executions"]==()



def test_dashboard_projects_effective_workflow_profile_without_exposing_payload():
    import sqlite3
    from portable.progress_journal import ProgressJournal
    from portable.workflow_profile import WorkflowProfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        auren_dir = root / ".auren"
        auren_dir.mkdir()
        database = auren_dir / "execution.sqlite3"
        profile = {
            "profile_version": "auren-workflow-v1",
            "delivery_mode": "stage_only",
            "execution_mode": "autonomous",
            "approval_mode": "before_push_or_pr",
            "review_mode": "all_applicable",
            "communication_mode": "concise",
            "recovery_mode": "checkpoint",
        }
        secret_payload_value = "must-not-be-rendered"
        payload = {
            "business_input": secret_payload_value,
            "_auren_workflow_profile": profile,
            "_auren_workflow_profile_digest": WorkflowProfile.from_mapping(profile).digest,
        }
        tampered_payload = {
            "_auren_workflow_profile": profile,
            "_auren_workflow_profile_digest": "a" * 64,
        }
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT INTO tasks (id,payload) VALUES (?,?)", ("profile-task", json.dumps(payload)))
            db.execute("INSERT INTO tasks (id,payload) VALUES (?,?)", ("tampered-task", json.dumps(tampered_payload)))
        journal = ProgressJournal(auren_dir / "progress.jsonl")
        for task_id in ("profile-task", "tampered-task"):
            journal.record(
                task_id, "task_submitted", state="queued", stage="queued",
                message="Task durably queued; execution has not started.",
            )

        snapshot = EngineeringDashboard(root).snapshot().as_dict()
        progress = snapshot["tasks"]["progress"]
        row = next(item for item in progress if item["task_id"] == "profile-task")
        assert row["workflow_profile_status"] == "verified"
        assert row["workflow_profile"]["profile_version"] == "auren-workflow-v1"
        assert row["workflow_profile"]["delivery_mode"] == "stage_only"
        assert row["workflow_profile"]["execution_mode"] == "autonomous"
        assert row["workflow_profile"]["approval_mode"] == "before_push_or_pr"
        assert row["workflow_profile"]["profile_digest"] == payload["_auren_workflow_profile_digest"]
        assert row["next_action"]
        tampered = next(item for item in progress if item["task_id"] == "tampered-task")
        assert tampered["workflow_profile_status"] == "invalid"
        assert "workflow_profile" not in tampered
        assert secret_payload_value not in json.dumps(snapshot)

def test_dashboard_reports_active_execution():
    with tempfile.TemporaryDirectory() as tmp:
        dashboard=EngineeringDashboard(tmp)
        dashboard.record_event(run_id="active-1",event="executing",task_id="task-7",status="running")
        payload=dashboard.snapshot().as_dict()
        assert len(payload["executions"])==1
        assert payload["executions"][0]["run_id"]=="active-1"

def test_dashboard_event_file_is_jsonl_and_durable():
    with tempfile.TemporaryDirectory() as tmp:
        dashboard=EngineeringDashboard(tmp)
        dashboard.record_event(run_id="r2",event="verify",status="completed",metadata={"hat":"quality"})
        data=__import__("json").loads(dashboard.event_path.read_text(encoding="utf-8").strip())
        assert data["run_id"]=="r2"
        assert data["metadata"]["hat"]=="quality"

def test_dashboard_reads_durable_active_engineering_episode():
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); state=root/"state"; state.mkdir()
        db_path=state/"auren.sqlite3"
        with sqlite3.connect(db_path) as db:
            db.execute("""CREATE TABLE engineering_episodes(
                project TEXT, episode_id TEXT, task_family TEXT, capability TEXT,
                state TEXT, iteration INTEGER, plan_digest TEXT, evidence_json TEXT,
                remediation_json TEXT, evolution_triggered INTEGER, terminal_action TEXT,
                last_error TEXT, updated_at TEXT, PRIMARY KEY(project,episode_id))""")
            db.execute("INSERT INTO engineering_episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("demo","ep-1","bugfix","python","verifying",2,"plan","[]","[]",0,"","", "2026-09-28T00:00:00+00:00"))
        snapshot=EngineeringDashboard(root).snapshot().as_dict()
        assert snapshot["executions"][0]["episode_id"]=="ep-1"
        assert snapshot["executions"][0]["status"]=="verifying"


def _create_plan_run(root, run_id="plan-run-active"):
    from portable.engineering_plan import EngineeringPlan, PlanTask
    from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
    from portable.engineering_plan_store import EngineeringPlanStore
    from portable.engineering_recovery import EngineeringRecoveryLedger
    plan = EngineeringPlan(
        run_id=run_id,
        goal="Make dashboard reflect durable plan state",
        requirements=("Plan state must be visible after restart",),
        tasks=(
            PlanTask("inspect", "Inspect persisted state", ("Digest verified",), "Restore the prior dashboard projection"),
            PlanTask("implement", "Implement projection", ("Tests pass",), "Revert the projection change", depends_on=("inspect",)),
        ),
        source_revision="a" * 40,
        budget={"dispatches": 4},
    )
    state_dir = root / ".auren"
    store = EngineeringPlanStore(state_dir / "plans.sqlite3")
    bridge = EngineeringPlanRecoveryBridge(
        store=store, ledger=EngineeringRecoveryLedger(state_dir / "recovery.sqlite3"),
    )
    run = bridge.start(plan)
    return plan, run, store, bridge


def test_dashboard_projects_durable_plan_run_and_task_counts():
    from portable.engineering_plan import EngineeringPlanRun
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan, run, store, bridge = _create_plan_run(root)
        running = bridge.transition_task(plan.run_id, "inspect", "ready", reason="dependencies satisfied")
        active = bridge.transition_task(plan.run_id, "inspect", "running", reason="worker claimed task")

        snapshot = EngineeringDashboard(root).snapshot().as_dict()
        row = next(item for item in snapshot["plan_runs"] if item["run_id"] == plan.run_id)
        assert row["status"] == "running"
        assert row["revision"] == active.revision
        assert row["task_counts"] == {"running": 1, "pending": 1}
        assert row["plan_digest"] == plan.digest
        assert row["evidence_count"] == 0
        assert snapshot["tasks"]["plan_runs"] == 1
        assert snapshot["tasks"]["plan_tasks"] == 2
        assert snapshot["executions"][0]["run_id"] == plan.run_id


def test_dashboard_keeps_corrupt_durable_plan_visible_as_unknown_not_success():
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan, run, store, _bridge = _create_plan_run(root, "plan-run-corrupt")
        with sqlite3.connect(store.database_path) as db:
            db.execute("UPDATE engineering_plan_runs SET run_json='{}' WHERE run_id=?", (plan.run_id,))
        snapshot = EngineeringDashboard(root).snapshot().as_dict()
        row = next(item for item in snapshot["plan_runs"] if item["run_id"] == plan.run_id)
        assert row["status"] == "unknown"
        assert "reconciliation required" in row["detail"]
        assert row["status"] != "completed"

def test_canonical_terminal_plan_status_suppresses_stale_running_event():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan, run, store, bridge = _create_plan_run(root, "plan-run-terminal")
        current = run
        for task_id in ("inspect", "implement"):
            for status in ("ready", "running", "passed"):
                current = bridge.transition_task(
                    plan.run_id, task_id, status,
                    reason=f"test canonical transition to {status}",
                )

        # Simulate an event projection that missed the terminal transition.
        dashboard = EngineeringDashboard(root)
        dashboard.record_event(
            run_id=plan.run_id, event="stale-heartbeat", task_id="inspect",
            status="running", detail="old event projection",
        )
        snapshot = dashboard.snapshot().as_dict()
        canonical = next(row for row in snapshot["plan_runs"] if row["run_id"] == plan.run_id)
        assert canonical["status"] == "completed"
        assert all(row.get("run_id") != plan.run_id for row in snapshot["executions"])



def test_dashboard_event_redacts_nested_secrets_and_avoids_object_repr():
    class SecretObject:
        def __repr__(self):
            return "leaked-secret-repr"

    with tempfile.TemporaryDirectory() as tmp:
        dashboard = EngineeringDashboard(tmp)
        dashboard.record_event(
            run_id="privacy-run",
            event="provider-result",
            detail="Bearer super-secret-token",
            metadata={
                "api_key": "should-not-be-written",
                "nested": {
                    "authorization": "Bearer nested-secret",
                    "note": "password=hunter2",
                    "opaque": SecretObject(),
                },
            },
        )
        raw = dashboard.event_path.read_text(encoding="utf-8")
        row = json.loads(raw)
        assert "super-secret-token" not in raw
        assert "should-not-be-written" not in raw
        assert "nested-secret" not in raw
        assert "hunter2" not in raw
        assert "leaked-secret-repr" not in raw
        assert row["metadata"]["api_key"] == "[REDACTED]"
        assert row["metadata"]["nested"]["authorization"] == "[REDACTED]"
        assert row["metadata"]["nested"]["opaque"] == "[unsupported:SecretObject]"


def test_dashboard_event_appends_are_serialized_across_concurrent_writers():
    with tempfile.TemporaryDirectory() as tmp:
        dashboard = EngineeringDashboard(tmp)

        def write_event(index):
            dashboard.record_event(
                run_id=f"concurrent-{index}",
                event="phase-progress",
                task_id=f"task-{index}",
                status="running",
                metadata={"index": index, "phase": "verify"},
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write_event, range(80)))

        lines = dashboard.event_path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        assert len(rows) == 80
        assert {row["run_id"] for row in rows} == {f"concurrent-{i}" for i in range(80)}
        assert all(row["event"] == "phase-progress" for row in rows)


def test_dashboard_event_payload_has_a_hard_encoded_size_limit():
    with tempfile.TemporaryDirectory() as tmp:
        dashboard = EngineeringDashboard(tmp)
        metadata = {f"field-{index}": "x" * 2000 for index in range(100)}
        try:
            dashboard.record_event(run_id="oversized", event="large", metadata=metadata)
        except ValueError as exc:
            assert "64 KiB" in str(exc)
        else:
            raise AssertionError("oversized event must be rejected")
        assert not dashboard.event_path.exists()



def _write_dashboard_task_store(path, rows):
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')"
        )
        db.executemany(
            "INSERT INTO tasks (id,state,updated_at,payload) VALUES (?,?,?,?)",
            [(task_id, state, "2026-10-29T14:00:00+00:00", "{}") for task_id, state in rows],
        )


def test_dashboard_does_not_claim_terminal_success_when_journal_disagrees_with_controller(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard
    from portable.progress_journal import ProgressJournal

    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: tmp_path / "home")
    state_dir = tmp_path / ".auren"
    _write_dashboard_task_store(state_dir / "execution.sqlite3", [("task-1", "completed")])
    journal = ProgressJournal(state_dir / "progress.jsonl")
    journal.record("task-1", "attempt_started", state="running", stage="dispatch", message="worker started")

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "task-1")
    assert row["state"] == "unknown"
    assert row["canonical_state"] == "completed"
    assert row["reported_state"] == "running"
    assert row["status_category"] == "unknown"
    assert row["status_conflict"] is True
    assert row["next_action"]


def test_dashboard_uses_canonical_pending_state_over_stale_journal_projection(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard
    from portable.progress_journal import ProgressJournal

    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: tmp_path / "home")
    state_dir = tmp_path / ".auren"
    _write_dashboard_task_store(state_dir / "execution.sqlite3", [("task-pending", "pending")])
    (state_dir / "progress.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        ProgressJournal, "list_snapshots",
        lambda self, stale_after_seconds=60: [{
            "task_id": "task-pending", "state": "stale", "reported_state": "running",
            "stale": True, "has_progress": True, "last_update": "2026-10-29T12:00:00+00:00",
            "seconds_since_update": 7200, "progress_percent": None, "completed_units": None,
            "total_units": None, "event_count": 2, "next_action": "inspect worker health",
        }],
    )

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "task-pending")
    assert row["state"] == "queued"
    assert row["canonical_state"] == "pending"
    assert row["status_category"] == "queued"
    assert row["stale"] is False
    assert row["status_conflict"] is False


def test_dashboard_fails_closed_when_local_and_executive_task_stores_disagree(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard

    home = tmp_path / "home"
    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: home)
    _write_dashboard_task_store(tmp_path / ".auren" / "execution.sqlite3", [("shared-task", "blocked_reconciliation")])
    _write_dashboard_task_store(home / "executive" / ".auren" / "execution.sqlite3", [("shared-task", "cancelled")])

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "shared-task")
    assert row["state"] == "unknown"
    assert row["status_category"] == "unknown"
    assert row["status_conflict"] is True
    assert "reconcile" in row["next_action"]


def test_dashboard_preserves_cancelled_and_failed_without_inventing_timeout_or_score(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard
    from portable.progress_journal import ProgressJournal

    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: tmp_path / "home")
    states = [("task-cancelled", "cancelled"), ("task-failed", "failed")]
    state_dir = tmp_path / ".auren"
    _write_dashboard_task_store(state_dir / "execution.sqlite3", states)
    journal = ProgressJournal(state_dir / "progress.jsonl")
    for task_id, state in states:
        journal.record(task_id, "task_terminal", state=state, stage="terminal", message="durable outcome")

    dashboard = EngineeringDashboard(tmp_path)
    rows = dashboard.snapshot().as_dict()["tasks"]["progress"]
    by_id = {item["task_id"]: item for item in rows}
    for task_id, state in states:
        assert by_id[task_id]["state"] == state
        assert by_id[task_id]["status_category"] == state
        assert by_id[task_id]["status_conflict"] is False
    assert dashboard._progress_status_category("timed_out") == "timed_out"
    assert dashboard._progress_status_category("unscored") == "unscored"


def test_dashboard_missing_terminal_journal_is_unknown_not_success(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard

    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: tmp_path / "home")
    _write_dashboard_task_store(tmp_path / ".auren" / "execution.sqlite3", [("task-no-journal", "completed")])

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "task-no-journal")
    assert row["state"] == "unknown"
    assert row["canonical_state"] == "completed"
    assert row["status_conflict"] is True
    assert row["has_progress"] is False


def test_dashboard_unreadable_configured_task_store_blocks_terminal_projection(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard
    from portable.progress_journal import ProgressJournal

    home = tmp_path / "home"
    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: home)
    state_dir = tmp_path / ".auren"
    _write_dashboard_task_store(state_dir / "execution.sqlite3", [("task-1", "completed")])
    journal = ProgressJournal(state_dir / "progress.jsonl")
    journal.record("task-1", "task_terminal", state="completed", stage="terminal", message="receipt")
    unreadable = home / "executive" / ".auren" / "execution.sqlite3"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_bytes(b"not a sqlite database")

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "task-1")
    assert row["state"] == "unknown"
    assert row["status_category"] == "unknown"
    assert row["status_conflict"] is True
    assert "unreadable canonical task store" in row["next_action"]
    assert any(item["task_id"] == "canonical-task-store-integrity" for item in rows)


def test_dashboard_adds_status_category_to_journal_only_rows(tmp_path, monkeypatch):
    from portable.engineering_dashboard import EngineeringDashboard
    from portable.progress_journal import ProgressJournal

    monkeypatch.setattr("portable.engineering_dashboard.canonical_auren_home", lambda: tmp_path / "home")
    state_dir = tmp_path / ".auren"
    journal = ProgressJournal(state_dir / "progress.jsonl")
    journal.record("journal-only", "approval_requested", state="blocked", stage="approval", message="waiting for approval")

    rows = EngineeringDashboard(tmp_path).snapshot().as_dict()["tasks"]["progress"]
    row = next(item for item in rows if item["task_id"] == "journal-only")
    assert row["status_category"] == "blocked"
    assert row["state_source"] == "progress_journal"
    assert row["canonical_state"] is None
