"""Bounded response-delivery worker admission regressions."""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from portable import response_delivery_supervisor as supervisor_module


class _Controller:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _load_handler(self, _name):
        raise KeyError("not registered")

    def register_handler(self, *_args, **_kwargs):
        return None

    def task(self, _task_id):
        return SimpleNamespace(state="pending")


def _process_slot_contender(db_path, acquired_queue, release_event):
    delivery = object.__new__(supervisor_module.ResponseDeliverySupervisor)
    delivery.registry_path = Path(db_path).parent / "response_delivery_registry.sqlite3"
    delivery.admission_coordinator = None
    delivery.deployment_host_id = None
    delivery._deployment_leases = {}
    task_id = f"process-{os.getpid()}"
    token = delivery._acquire_worker_slot(task_id)
    acquired_queue.put(token is not None)
    release_event.wait(10)
    if token is not None:
        delivery._release_worker_slot(task_id, token)


class _FakeAdmissionCoordinator:
    def __init__(self):
        self.leases = {}
        self.fail = False
        self.calls = []

    def acquire(self, resource, owner, operation_id, *, capacity, lease_seconds):
        self.calls.append(("acquire", resource, owner, operation_id, capacity, lease_seconds))
        if self.fail:
            raise RuntimeError("coordinator unavailable")
        if len(self.leases) >= capacity or operation_id in self.leases:
            return None
        lease = SimpleNamespace(resource=resource, owner=owner, operation_id=operation_id,
                                token="fenced-" + operation_id, generation=1)
        self.leases[operation_id] = lease
        return lease

    def renew(self, lease, *, lease_seconds):
        self.calls.append(("renew", lease.operation_id, lease_seconds))
        return self.leases.get(lease.operation_id) == lease

    def release(self, lease):
        self.calls.append(("release", lease.operation_id))
        if self.leases.get(lease.operation_id) != lease:
            return False
        del self.leases[lease.operation_id]
        return True


class ResponseDeliverySupervisorCapacityTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _supervisor(self, name="progress.jsonl"):
        return supervisor_module.ResponseDeliverySupervisor(
            _Controller(self.root / "execution.sqlite3"), self.root / name
        )

    def test_worker_admission_is_bounded_and_saturation_keeps_tasks_pending(self):
        with patch.object(supervisor_module, "MAX_ACTIVE_DELIVERY_WORKERS", 1):
            controller = _Controller(self.root / "execution.sqlite3")
            delivery = supervisor_module.ResponseDeliverySupervisor(
                controller, self.root / "progress.jsonl"
            )
            first_token = delivery._acquire_worker_slot("task-one")
            second_token = delivery._acquire_worker_slot("task-two")

            self.assertIsNotNone(first_token)
            self.assertIsNone(second_token)
            self.assertEqual(
                delivery.shared_capacity_snapshot(),
                {
                    "active_workers": 1,
                    "worker_limit": 1,
                    "available_workers": 0,
                    "saturated": True,
                },
            )
            self.assertEqual(controller.task("task-two").state, "pending")
            delivery._release_worker_slot("task-one", first_token)

    def test_shared_worker_slots_enforce_quota_and_expire_after_crash(self):
        with patch.object(supervisor_module, "MAX_ACTIVE_DELIVERY_WORKERS", 1):
            first = self._supervisor("first-progress.jsonl")
            second = self._supervisor("second-progress.jsonl")
            first_token = first._acquire_worker_slot("task-one")
            self.assertIsNotNone(first_token)
            self.assertTrue(first.shared_capacity_snapshot()["saturated"])
            self.assertIsNone(second._acquire_worker_slot("task-two"))

            # A crashed process leaves a bounded lease, not a permanent capacity leak.
            with sqlite3.connect(first.registry_path) as db:
                db.execute(
                    "UPDATE delivery_worker_slots SET lease_until=? WHERE task_id=?",
                    ("2000-01-01T00:00:00+00:00", "task-one"),
                )
            second_token = second._acquire_worker_slot("task-two")
            self.assertIsNotNone(second_token)
            first._release_worker_slot("task-two", first_token)
            self.assertEqual(second.shared_capacity_snapshot()["active_workers"], 1)
            second._release_worker_slot("task-two", second_token)
            self.assertEqual(second.shared_capacity_snapshot()["active_workers"], 0)

    def test_worker_slot_renewal_requires_matching_owner_and_extends_lease(self):
        delivery = self._supervisor()
        token = delivery._acquire_worker_slot("task-one")
        self.assertIsNotNone(token)
        with sqlite3.connect(delivery.registry_path) as db:
            before = db.execute(
                "SELECT lease_until FROM delivery_worker_slots WHERE task_id=?",
                ("task-one",),
            ).fetchone()[0]
        self.assertFalse(delivery._renew_worker_slot("task-one", "wrong-owner"))
        self.assertTrue(delivery._renew_worker_slot("task-one", token))
        with sqlite3.connect(delivery.registry_path) as db:
            after = db.execute(
                "SELECT lease_until FROM delivery_worker_slots WHERE task_id=?",
                ("task-one",),
            ).fetchone()[0]
        self.assertGreater(after, before)
        delivery._release_worker_slot("task-one", token)

    def test_real_processes_share_the_eight_worker_quota(self):
        context = multiprocessing.get_context("spawn")
        acquired_queue = context.Queue()
        release_event = context.Event()
        db_path = str(self.root / "execution.sqlite3")
        # Create the shared registry schema before child processes contend.
        self._supervisor("initializer-progress.jsonl")
        processes = [
            context.Process(
                target=_process_slot_contender,
                args=(db_path, acquired_queue, release_event),
            )
            for _ in range(supervisor_module.MAX_ACTIVE_DELIVERY_WORKERS + 1)
        ]
        for process in processes:
            process.start()
        try:
            results = [acquired_queue.get(timeout=15) for _ in processes]
            self.assertEqual(sum(results), supervisor_module.MAX_ACTIVE_DELIVERY_WORKERS)
        finally:
            release_event.set()
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
        self.assertTrue(all(process.exitcode == 0 for process in processes))

    def test_shared_coordinator_is_required_and_renews_and_releases_fenced_lease(self):
        coordinator = _FakeAdmissionCoordinator()
        delivery = supervisor_module.ResponseDeliverySupervisor(
            _Controller(self.root / "execution.sqlite3"), self.root / "progress.jsonl",
            admission_coordinator=coordinator, deployment_host_id="host-a",
        )
        token = delivery._acquire_worker_slot("task-shared")
        self.assertIsNotNone(token)
        self.assertEqual(len(coordinator.leases), 1)
        self.assertTrue(delivery._renew_worker_slot("task-shared", token))
        delivery._release_worker_slot("task-shared", token)
        self.assertEqual(coordinator.leases, {})
        self.assertEqual([call[0] for call in coordinator.calls], ["acquire", "renew", "release"])

    def test_shared_coordinator_outage_fails_closed_without_local_fallback(self):
        coordinator = _FakeAdmissionCoordinator()
        coordinator.fail = True
        delivery = supervisor_module.ResponseDeliverySupervisor(
            _Controller(self.root / "execution.sqlite3"), self.root / "progress.jsonl",
            admission_coordinator=coordinator, deployment_host_id="host-a",
        )
        with self.assertRaises(RuntimeError):
            delivery._acquire_worker_slot("task-outage")
        self.assertEqual(delivery.shared_capacity_snapshot()["active_workers"], 0)

    def test_shared_admission_requires_stable_host_identity(self):
        with self.assertRaises(ValueError):
            supervisor_module.ResponseDeliverySupervisor(
                _Controller(self.root / "execution.sqlite3"), self.root / "progress.jsonl",
                admission_coordinator=_FakeAdmissionCoordinator(),
            )

    def test_duplicate_worker_slot_claim_does_not_allocate_a_second_slot(self):
        delivery = self._supervisor()
        first_token = delivery._acquire_worker_slot("task-one")
        duplicate_token = delivery._acquire_worker_slot("task-one")
        self.assertIsNotNone(first_token)
        self.assertIsNone(duplicate_token)
        self.assertEqual(delivery.shared_capacity_snapshot()["active_workers"], 1)
        delivery._release_worker_slot("task-one", first_token)


if __name__ == "__main__":
    unittest.main()
