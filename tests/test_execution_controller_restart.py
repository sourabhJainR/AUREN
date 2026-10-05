from pathlib import Path

from portable.execution_controller import ExecutionController

def test_resume_pending_task_after_controller_restart(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler)
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "restart"})
    second = ExecutionController(tmp_path)
    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"

def durable_handler(payload):
    return {"success": True, "evidence": [payload["id"]]}
