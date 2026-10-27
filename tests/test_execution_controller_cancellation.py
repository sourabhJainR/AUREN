from __future__ import annotations

import threading
import time
from pathlib import Path

from portable.execution_controller import ExecutionController


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


def _register(controller: ExecutionController, handler, *, execution_class="read_only") -> str:
    name = f"{handler.__module__}.{handler.__name__}"
    controller.register_handler(name, handler, execution_class=execution_class)
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
    name = _register(controller, supervised_child_handler, execution_class="consequential")
    task = controller.submit(
        name, {"provider_cwd": str(tmp_path)}, deadline_seconds=1.5,
    )
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
    worker.join(5)
    assert not worker.is_alive(), "provider child did not stop before the bounded test timeout"
    assert not failures, failures
    current = controller.task(task.id)
    assert current is not None and current.state == "blocked_reconciliation"
    assert heartbeat.exists(), "provider child did not begin bounded work"
    deadline_marker = tmp_path / "provider-deadline-epoch"
    assert deadline_marker.is_file(), "provider child did not receive the supervisor deadline"
    from datetime import datetime
    propagated = float(deadline_marker.read_text(encoding="utf-8"))
    expected = datetime.fromisoformat(task.deadline_at).timestamp()
    assert abs(propagated - expected) < 0.5, "provider received a timeout unrelated to the supervisor deadline"
    _assert_provider_child_stopped(heartbeat)

