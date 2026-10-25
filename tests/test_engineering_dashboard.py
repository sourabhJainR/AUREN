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
    from portable.engineering_plan_store import EngineeringPlanStore
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
    store = EngineeringPlanStore(root / ".auren" / "plans.sqlite3")
    run = store.create(plan)
    return plan, run, store


def test_dashboard_projects_durable_plan_run_and_task_counts():
    from portable.engineering_plan import EngineeringPlanRun
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan, run, store = _create_plan_run(root)
        running = run.transition_task("inspect", "ready", plan=plan, reason="dependencies satisfied")
        store.save_run(plan.run_id, running, expected_revision=run.revision)
        active = running.transition_task("inspect", "running", plan=plan, reason="worker claimed task")
        store.save_run(plan.run_id, active, expected_revision=running.revision)

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
        plan, run, store = _create_plan_run(root, "plan-run-corrupt")
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
        plan, run, store = _create_plan_run(root, "plan-run-terminal")
        current = run
        for task_id in ("inspect", "implement"):
            for status in ("ready", "running", "passed"):
                updated = current.transition_task(
                    task_id, status, plan=plan,
                    reason=f"test canonical transition to {status}",
                )
                store.save_run(plan.run_id, updated, expected_revision=current.revision)
                current = updated

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
