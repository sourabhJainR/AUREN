from __future__ import annotations

import threading
import time
from pathlib import Path

from portable.execution_controller import ExecutionController
from portable.provider_operation_lifecycle import ProviderOutcomeBlocked


_HANDLER_STARTED = threading.Event()


def cancellation_aware_handler(payload):
    _HANDLER_STARTED.set()
    event = payload.get("auren_cancel_event")
    if not isinstance(event, threading.Event):
        raise AssertionError("supervisor must provide a live cancellation Event")
    while not event.wait(0.01):
        time.sleep(0.005)
    return {"success": True, "detail": "handler returned after cancellation"}


def should_not_run_handler(payload):
    raise AssertionError("cancelled task must not dispatch its handler")


def supervised_child_handler(payload):
    import sys
    from portable.subprocess_provider import SubprocessProviderInvoker

    script = (
        "from pathlib import Path; import os, time\n"
        "Path('provider-started').write_text('yes', encoding='utf-8')\n"
        "Path('provider-deadline-epoch').write_text(os.environ.get('AUREN_DEADLINE_EPOCH', 'missing'), encoding='utf-8')\n"
        "i = 0\n"
        "while True:\n"
        "    Path('provider-heartbeat').write_text(str(i), encoding='utf-8')\n"
        "    i += 1\n"
        "    time.sleep(0.02)\n"
    )
    invoker = SubprocessProviderInvoker(
        [sys.executable, "-c", script],
        cwd=payload["provider_cwd"],
    )
    output = invoker.invoke_supervised(
        "bounded supervisor integration probe", 30, execution_payload=payload,
    )
    return {"success": True, "detail": output, "evidence": ["subprocess-provider:completed"]}


def _register(controller: ExecutionController, handler) -> str:
    name = f"{handler.__module__}.{handler.__name__}"
    controller.register_handler(name, handler, execution_class="read_only")
    return name


def test_durable_cancellation_reaches_running_handler_and_rejects_late_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = _register(controller, cancellation_aware_handler)
    task = controller.submit(name, {"test": "running-cancellation"})
    failures = []
    _HANDLER_STARTED.clear()

    def run():
        try:
            controller.run_once(task.id)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert _HANDLER_STARTED.wait(5), "handler did not begin within the test budget"
    assert controller.request_cancellation(task.id, reason="test cancellation")
    worker.join(5)
    assert not worker.is_alive(), "handler did not observe cancellation within the bound"
    assert not failures, failures
    current = controller.task(task.id)
    assert current is not None and current.state == "cancelled"
    assert not controller.request_cancellation(task.id), "terminal cancellation must be idempotent"


def test_cancellation_requested_before_dispatch_never_runs_handler(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = _register(controller, should_not_run_handler)
    task = controller.submit(name, {"test": "pre-dispatch-cancellation"})
    assert controller.request_cancellation(task.id, reason="cancel before worker start")
    result = controller.run_once(task.id)
    assert result is None
    current = controller.task(task.id)
    assert current is not None and current.state == "cancelled"


def _wait_for_provider_start(marker: Path, worker: threading.Thread, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and worker.is_alive() and not marker.exists():
        time.sleep(0.01)
    assert marker.exists(), "supervised provider child did not launch within the test budget"


def _assert_provider_child_stopped(heartbeat: Path) -> None:
    last_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.12)
    assert heartbeat.read_text(encoding="utf-8") == last_value, "provider child continued after supervisor stop"


def test_supervisor_cancellation_kills_real_subprocess_provider_child(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = _register(controller, supervised_child_handler)
    task = controller.submit(name, {"provider_cwd": str(tmp_path)})
    failures = []

    def run():
        try:
            controller.run_once(task.id)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    marker = tmp_path / "provider-started"
    heartbeat = tmp_path / "provider-heartbeat"
    _wait_for_provider_start(marker, worker)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not heartbeat.exists():
        time.sleep(0.01)
    assert heartbeat.exists(), "provider child did not begin bounded work"

    assert controller.request_cancellation(task.id, reason="cancel real supervised provider child")
    worker.join(5)
    assert not worker.is_alive(), "provider child did not stop after supervisor cancellation"
    assert not failures, failures
    current = controller.task(task.id)
    assert current is not None and current.state == "cancelled"
    _assert_provider_child_stopped(heartbeat)


def test_supervisor_deadline_kills_real_subprocess_provider_child(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    name = _register(controller, supervised_child_handler)
    # Windows CI can take longer to create and assign the Job Object than
    # the short deadline used by this test. Keep the deadline finite but leave
    # enough time for the child to launch before verifying deadline termination.
    task = controller.submit(
        name, {"provider_cwd": str(tmp_path)}, deadline_seconds=8.0,
    )
    claimed = controller._claim(task.id)
    assert claimed is not None
    handler = controller._load_handler(name)
    failures = []

    def run():
        try:
            controller._invoke_consequential_with_ledger(claimed, handler)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    marker = tmp_path / "provider-started"
    heartbeat = tmp_path / "provider-heartbeat"
    _wait_for_provider_start(marker, worker, timeout=6.0)
    worker.join(10)
    assert not worker.is_alive(), "provider child did not stop before the bounded test timeout"
    assert len(failures) == 1 and isinstance(failures[0], ProviderOutcomeBlocked), failures
    current = controller.task(task.id)
    assert current is not None and current.state == "running"
    assert heartbeat.exists(), "provider child did not begin bounded work"
    deadline_marker = tmp_path / "provider-deadline-epoch"
    assert deadline_marker.is_file(), "provider child did not receive the supervisor deadline"
    from datetime import datetime
    propagated = float(deadline_marker.read_text(encoding="utf-8"))
    expected = datetime.fromisoformat(task.deadline_at).timestamp()
    assert abs(propagated - expected) < 0.5, "provider received a timeout unrelated to the supervisor deadline"
    _assert_provider_child_stopped(heartbeat)



# End-to-end supervisor -> GraphAgentTeam -> model-router probe. These are local
# stubbed inference calls; they prove control propagation, not a real provider outcome.
_GRAPH_ROUTER_STARTED = threading.Event()
_GRAPH_ROUTER_REQUESTS = []
_GRAPH_ROUTER_RESULTS = []
_GRAPH_ROUTER_GUARD_IDENTITIES = []
_GRAPH_ROUTER_GUARD_REMAINING = []
_GRAPH_ROUTER_DEADLINES = []


class _EmptyModelDiscovery:
    base_url = "http://127.0.0.1:11434"

    def discover(self):
        return {"available": False, "models": []}


class _SupervisedRouterAgent:
    configuration_digest = "test-only-router-adapter-v1"
    implementation_version = "test-only"

    def __init__(self, execution_payload):
        self.execution_payload = execution_payload

    def propose_with_execution_guard(self, request, *, execution_guard, execution_guard_persist=None):
        from portable.hybrid_model_router import HybridModelRouter, TaskRouteRequest

        cancellation = self.execution_payload.get("auren_cancel_event")
        if not isinstance(cancellation, threading.Event):
            raise AssertionError("controller cancellation event was not propagated to the graph agent")
        _GRAPH_ROUTER_GUARD_IDENTITIES.append(execution_guard.cancellation_event is cancellation)
        guard_snapshot = execution_guard.snapshot()
        _GRAPH_ROUTER_GUARD_REMAINING.append(guard_snapshot.get("remaining_seconds"))
        _GRAPH_ROUTER_DEADLINES.append(self.execution_payload.get("auren_deadline_epoch"))

        def remote_executor(route_request):
            _GRAPH_ROUTER_REQUESTS.append(route_request)
            _GRAPH_ROUTER_STARTED.set()
            cancellation.wait(timeout=20)
            return "late provider result after supervisor stop"

        router = HybridModelRouter(
            discovery=_EmptyModelDiscovery(),
            remote_executor=remote_executor,
            resource_reader=lambda: {},
        )
        result = router.route_supervised(
            TaskRouteRequest(
                request["task"], "general", privacy="public",
                max_attempts=3, timeout_seconds=30,
            ),
            execution_payload=self.execution_payload,
            execution_guard=execution_guard,
            execution_guard_persist=execution_guard_persist,
        )
        _GRAPH_ROUTER_RESULTS.append(result)
        accepted = bool(result.success)
        return {
            "schema_version": "auren-crew-proposal-v1",
            "recommendation": "model route completed" if accepted else "model route blocked after supervisor stop",
            "artifacts": [],
            "evidence_refs": ["test://model-router/result"] if accepted else [],
            "uncertainty": [] if accepted else ["late model result rejected by shared execution guard"],
            "blockers": [] if accepted else ["supervisor cancellation/deadline"],
            "acceptance_check_results": {
                "route_checked": {
                    "status": "pass" if accepted else "blocked",
                    "evidence_refs": ["test://model-router/result"] if accepted else [],
                }
            },
        }


def _dispatch_graph_router_from_state(state):
    dispatch = state.get("auren_crew_dispatch")
    if not callable(dispatch):
        raise AssertionError("GraphAgentTeam did not bind its canonical crew dispatcher")
    return {
        "router_dispatch": dispatch(
            "model_router", "public model router integration request", {},
        )
    }


def supervised_graph_router_handler(payload):
    from portable.orchestration import Node, NodeKind
    from portable.state_graph import CrewDefinition, GraphAgentTeam, StateGraph

    graph = StateGraph.from_nodes([
        Node("route", NodeKind.DETERMINISTIC, _dispatch_graph_router_from_state),
    ])
    crew = CrewDefinition(
        crew_id="model_router",
        specialty="model routing",
        triggers=("model router",),
        instructions="Route one public inference request and return a structured proposal.",
        required_artifacts=(),
        allowed_authorities=("propose",),
        acceptance_checks=("route_checked",),
        always_on=True,
    )
    team = GraphAgentTeam(
        graph,
        agents={"model_router": _SupervisedRouterAgent(payload)},
        crews={"model_router": crew},
    )
    work_key = payload.get("work_key") or "AUREN:issue-696:continuous-engineering"
    result = team.run_supervised(
        str(work_key), "model router integration", {},
        execution_payload=payload,
    )
    return {
        "success": getattr(result.status, "value", "") == "accepted",
        "status": getattr(result.status, "value", "unknown"),
    }


def _reset_graph_router_probe():
    _GRAPH_ROUTER_STARTED.clear()
    _GRAPH_ROUTER_REQUESTS.clear()
    _GRAPH_ROUTER_RESULTS.clear()
    _GRAPH_ROUTER_GUARD_IDENTITIES.clear()
    _GRAPH_ROUTER_GUARD_REMAINING.clear()
    _GRAPH_ROUTER_DEADLINES.clear()


def test_durable_cancellation_flows_through_graph_team_and_model_router(tmp_path: Path):
    _reset_graph_router_probe()
    controller = ExecutionController(tmp_path)
    name = _register(controller, supervised_graph_router_handler)
    task = controller.submit(
        name,
        {"work_key": "AUREN:issue-696:continuous-engineering", "scenario": "graph-router-cancellation"},
        work_key="AUREN:issue-696:continuous-engineering",
    )
    failures = []

    def run():
        try:
            controller.run_once(task.id)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert _GRAPH_ROUTER_STARTED.wait(6), "model router was not reached through the supervised graph"
    assert controller.request_cancellation(task.id, reason="cancel integrated graph/model-router request")
    worker.join(8)
    assert not worker.is_alive(), "graph/model-router execution ignored durable cancellation"
    assert not failures, failures
    current = controller.task(task.id)
    assert current is not None and current.state == "cancelled"
    assert len(_GRAPH_ROUTER_REQUESTS) == 1, "cancellation must not redispatch the provider route"
    assert len(_GRAPH_ROUTER_RESULTS) == 1 and not _GRAPH_ROUTER_RESULTS[0].success
    assert _GRAPH_ROUTER_GUARD_IDENTITIES == [True], "graph and router must share the controller cancellation Event"
    assert _GRAPH_ROUTER_DEADLINES == [None]


def test_absolute_deadline_flows_through_graph_team_and_model_router(tmp_path: Path):
    from datetime import datetime

    _reset_graph_router_probe()
    controller = ExecutionController(tmp_path)
    name = _register(controller, supervised_graph_router_handler)
    task = controller.submit(
        name,
        {"work_key": "AUREN:issue-696:continuous-engineering", "scenario": "graph-router-deadline"},
        work_key="AUREN:issue-696:continuous-engineering",
        deadline_seconds=8.0,
    )
    failures = []

    def run():
        try:
            controller.run_once(task.id)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert _GRAPH_ROUTER_STARTED.wait(6), "model router was not reached before the bounded deadline"
    worker.join(10)
    assert not worker.is_alive(), "graph/model-router execution exceeded the absolute supervisor deadline"
    assert not failures, failures
    current = controller.task(task.id)
    assert current is not None and current.state == "failed"
    assert len(_GRAPH_ROUTER_REQUESTS) == 1, "deadline expiry must not redispatch the provider route"
    assert len(_GRAPH_ROUTER_RESULTS) == 1 and not _GRAPH_ROUTER_RESULTS[0].success
    assert _GRAPH_ROUTER_GUARD_IDENTITIES == [True], "graph and router must share the controller cancellation Event"
    assert len(_GRAPH_ROUTER_DEADLINES) == 1 and _GRAPH_ROUTER_DEADLINES[0] is not None
    propagated = float(_GRAPH_ROUTER_DEADLINES[0])
    expected = datetime.fromisoformat(task.deadline_at).timestamp()
    assert abs(propagated - expected) < 0.5, "graph/router deadline must match the durable controller deadline"
    remaining = _GRAPH_ROUTER_GUARD_REMAINING[0]
    assert isinstance(remaining, (int, float)) and 0 < remaining <= 8.0
    assert 0 < _GRAPH_ROUTER_REQUESTS[0].timeout_seconds < 8.0, "model request timeout must be capped by the shared absolute deadline"



_TASK_ID_CAPTURE = []


def task_identity_handler(payload):
    _TASK_ID_CAPTURE.append(payload.get("auren_task_id"))
    return {"success": True, "detail": "supervisor task identity observed"}


def test_controller_injects_unspoofable_supervisor_task_identity(tmp_path: Path):
    _TASK_ID_CAPTURE.clear()
    controller = ExecutionController(tmp_path)
    name = _register(controller, task_identity_handler)
    task = controller.submit(name, {"auren_task_id": "caller-supplied-spoof"})
    controller.run_once(task.id)
    assert _TASK_ID_CAPTURE == [task.id]
