"""Bounded response-delivery worker admission regressions."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from portable import response_delivery_supervisor as supervisor_module


class _Controller:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _load_handler(self, _name):
        raise KeyError("not registered")

    def register_handler(self, *_args, **_kwargs):
        return None

    def task(self, _task_id):
        return SimpleNamespace(state="pending")


class _LiveThread:
    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        return None


def test_worker_admission_is_bounded_and_saturation_keeps_tasks_pending(tmp_path):
    supervisor_module._THREADS.clear()
    supervisor_module._SATURATION_COUNT = 0
    try:
        with patch.object(supervisor_module, "MAX_ACTIVE_DELIVERY_WORKERS", 1), patch.object(
            supervisor_module.threading, "Thread", _LiveThread
        ):
            controller = _Controller(tmp_path / "execution.sqlite3")
            delivery = supervisor_module.ResponseDeliverySupervisor(
                controller, tmp_path / "progress.jsonl"
            )

            assert delivery.start("task-one") is True
            assert delivery.start("task-two") is False

            snapshot = delivery.capacity_snapshot()
            assert snapshot == {
                "active_workers": 1,
                "worker_limit": 1,
                "available_workers": 0,
                "saturated": True,
                "saturation_events": 1,
            }
            # Admission failure does not mutate the durable task state.
            assert controller.task("task-two").state == "pending"
    finally:
        supervisor_module._THREADS.clear()
        supervisor_module._SATURATION_COUNT = 0


def test_starting_an_already_active_delivery_does_not_consume_another_slot(tmp_path):
    supervisor_module._THREADS.clear()
    supervisor_module._SATURATION_COUNT = 0
    try:
        with patch.object(supervisor_module, "MAX_ACTIVE_DELIVERY_WORKERS", 1), patch.object(
            supervisor_module.threading, "Thread", _LiveThread
        ):
            controller = _Controller(tmp_path / "execution.sqlite3")
            delivery = supervisor_module.ResponseDeliverySupervisor(
                controller, tmp_path / "progress.jsonl"
            )

            assert delivery.start("task-one") is True
            assert delivery.start("task-one") is True
            assert delivery.capacity_snapshot()["active_workers"] == 1
            assert delivery.capacity_snapshot()["saturation_events"] == 0
    finally:
        supervisor_module._THREADS.clear()
        supervisor_module._SATURATION_COUNT = 0
