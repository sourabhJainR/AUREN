"""Tests for the observation-only AUREN engineering dashboard."""
from pathlib import Path
import tempfile
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
