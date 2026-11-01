"""Bounded response-delivery worker admission regressions."""
from pathlib import Path
import sqlite3
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


def test_shared_worker_slots_enforce_cross_process_quota_and_expire_after_crash(tmp_path):
    with patch.object(supervisor_module, "MAX_ACTIVE_DELIVERY_WORKERS", 1):
        db_path = tmp_path / "execution.sqlite3"
        first = supervisor_module.ResponseDeliverySupervisor(
            _Controller(db_path), tmp_path / "first-progress.jsonl"
        )
        second = supervisor_module.ResponseDeliverySupervisor(
            _Controller(db_path), tmp_path / "second-progress.jsonl"
        )

        first_token = first._acquire_worker_slot("task-one")
        assert first_token is not None
        assert first.shared_capacity_snapshot()["saturated"] is True
        assert second._acquire_worker_slot("task-two") is None

        # A process crash leaves a bounded lease, not a permanent capacity leak.
        with sqlite3.connect(first.registry_path) as db:
            db.execute(
                "UPDATE delivery_worker_slots SET lease_until=? WHERE task_id=?",
                ("2000-01-01T00:00:00+00:00", "task-one"),
            )
        second_token = second._acquire_worker_slot("task-two")
        assert second_token is not None
        first._release_worker_slot("task-two", first_token)
        assert second.shared_capacity_snapshot()["active_workers"] == 1
        second._release_worker_slot("task-two", second_token)
        assert second.shared_capacity_snapshot()["active_workers"] == 0


def test_worker_slot_renewal_requires_matching_owner_and_extends_lease(tmp_path):
    db_path = tmp_path / "execution.sqlite3"
    delivery = supervisor_module.ResponseDeliverySupervisor(
        _Controller(db_path), tmp_path / "progress.jsonl"
    )
    token = delivery._acquire_worker_slot("task-one")
    assert token is not None
    with sqlite3.connect(delivery.registry_path) as db:
        before = db.execute(
            "SELECT lease_until FROM delivery_worker_slots WHERE task_id=?",
            ("task-one",),
        ).fetchone()[0]
    assert delivery._renew_worker_slot("task-one", "wrong-owner") is False
    assert delivery._renew_worker_slot("task-one", token) is True
    with sqlite3.connect(delivery.registry_path) as db:
        after = db.execute(
            "SELECT lease_until FROM delivery_worker_slots WHERE task_id=?",
            ("task-one",),
        ).fetchone()[0]
    assert after >= before
    delivery._release_worker_slot("task-one", token)


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
