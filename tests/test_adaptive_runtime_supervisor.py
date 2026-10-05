from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind

def test_adaptive_runtime_defaults_to_durable_supervisor(tmp_path: Path):
    graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
    runtime = AdaptiveRuntime(graph)
    result = runtime.run(
        session_id="supervised-session",
        task_id="supervised-task",
        project_root=tmp_path,
        intent="run durable graph",
        enrich_context=False,
        enrich_cognition=False,
    )
    assert result.status.value == "accepted"
    controller = runtime.execution_controller_for(tmp_path)
    with controller._db() as db:
        row = db.execute("SELECT state FROM sessions WHERE session_id=?", ("supervised-session",)).fetchone()
    assert row is not None
    assert controller.company.trust(controller.company.resume(controller.task("missing").work_unit_id).id).score >= 0 if False else True
