"""Default AdaptiveRuntime facade recovery across an actual process boundary."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_default_runtime_facade_recovers_expired_running_task_in_fresh_process(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    seed_script = r"""
import sys
from pathlib import Path
from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind
from tests.test_execution_controller import durable_handler
root = Path(sys.argv[1])
runtime = AdaptiveRuntime(Graph([Node("noop", NodeKind.DETERMINISTIC, lambda state: "ok")]),
                          state_root=root / "state")
name = "tests.test_execution_controller.durable_handler"
runtime.register_task_handler(name, durable_handler, execution_class="read_only")
task = runtime.submit_task(name, {"id": "adaptive-facade-restart"})
claim = runtime.execution_controller._claim(task.id)
assert claim is not None and claim.state == "running"
print(task.id)
"""
    seeded = subprocess.run(
        [sys.executable, "-c", seed_script, str(tmp_path)],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=15,
    )
    task_id = seeded.stdout.strip().splitlines()[-1]
    assert task_id

    recover_script = r"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind
from tests.test_execution_controller import durable_handler
root = Path(sys.argv[1])
task_id = sys.argv[2]
runtime = AdaptiveRuntime(Graph([Node("noop", NodeKind.DETERMINISTIC, lambda state: "ok")]),
                          state_root=root / "state")
name = "tests.test_execution_controller.durable_handler"
runtime.register_task_handler(name, durable_handler, execution_class="read_only")
live_lease = runtime.resume_tasks()
db_path = runtime.execution_controller.db_path
expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
with sqlite3.connect(db_path) as db:
    db.execute("UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, task_id))
recovered = runtime.resume_tasks()
task = runtime.execution_controller.task(task_id)
print(json.dumps({
    "live_lease_results": len(live_lease),
    "recovered_results": len(recovered),
    "state": task.state,
    "attempts": task.attempts,
}))
"""
    recovered = subprocess.run(
        [sys.executable, "-c", recover_script, str(tmp_path), task_id],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=15,
    )
    result = json.loads(recovered.stdout.strip().splitlines()[-1])
    assert result == {
        "live_lease_results": 0,
        "recovered_results": 1,
        "state": "completed",
        "attempts": 2,
    }
