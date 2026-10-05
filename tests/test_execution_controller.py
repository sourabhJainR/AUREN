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
