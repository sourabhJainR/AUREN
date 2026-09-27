"""Tests for the observation-only AER engineering dashboard."""
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
