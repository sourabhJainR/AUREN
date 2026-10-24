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
