"""Fail-closed shared response-delivery budget resume tests."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from portable.response_delivery_supervisor import ResponseDeliverySupervisor
from portable.runtime_recovery import RuntimeExecutionGuard


class _Controller:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.submissions = 0

    def _load_handler(self, _name):
        raise KeyError("not registered")

    def register_handler(self, *_args, **_kwargs):
        return None

    def submit(self, *_args, **_kwargs):
        self.submissions += 1
        return SimpleNamespace(id="delivery-task-1")

    def task(self, _task_id):
        return SimpleNamespace(state="pending")


class SharedDeliveryBudgetResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.controller = _Controller(self.root / "execution.sqlite3")
        self.supervisor = ResponseDeliverySupervisor(
            self.controller, self.root / "progress.jsonl"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_budget_id_without_existing_checkpoint_fails_before_task_dispatch(self):
        with self.assertRaisesRegex(FileNotFoundError, "cannot resume by ID alone"):
            self.supervisor.create(
                "a" * 64, "b" * 32, "a" * 64, budget_id="crew:missing"
            )
        self.assertEqual(0, self.controller.submissions)

    def test_budget_id_resumes_existing_checkpoint_and_charges_same_ledger(self):
        guard = RuntimeExecutionGuard(max_delivery_bytes=4096)
        with sqlite3.connect(self.supervisor.registry_path) as db:
            db.execute(
                "INSERT INTO delivery_budgets(budget_id,snapshot_json) VALUES(?,?)",
                ("crew:restored", __import__("json").dumps(guard.snapshot())),
            )

        task_id = self.supervisor.create(
            "c" * 64, "d" * 32, "c" * 64, budget_id="crew:restored"
        )
        self.assertEqual("delivery-task-1", task_id)
        self.assertTrue(self.supervisor.consume_delivery_bytes(
            task_id, 512, operation_key="delivery:resume:chunk-1"
        ))
        snapshot = self.supervisor.delivery_budget_snapshot(task_id)
        self.assertEqual(
            512, snapshot["delivery_charges"]["delivery:resume:chunk-1"]
        )
        self.assertEqual(1, self.controller.submissions)


    def test_missing_checkpoint_after_task_creation_blocks_chunk_charge(self):
        guard = RuntimeExecutionGuard(max_delivery_bytes=4096)
        task_id = self.supervisor.create(
            "1" * 64, "2" * 32, "1" * 64,
            execution_guard=guard, budget_id="crew:deleted-after-create",
        )
        with sqlite3.connect(self.supervisor.registry_path) as db:
            db.execute(
                "DELETE FROM delivery_budgets WHERE budget_id=?",
                ("crew:deleted-after-create",),
            )
        with self.assertRaisesRegex(RuntimeError, "checkpoint is unavailable"):
            self.supervisor.consume_delivery_bytes(
                task_id, 512, operation_key="delivery:deleted:chunk-1"
            )
        self.assertNotIn("delivery:deleted:chunk-1", guard.snapshot()["delivery_charges"])

    def test_corrupt_existing_checkpoint_fails_before_task_dispatch(self):
        with sqlite3.connect(self.supervisor.registry_path) as db:
            db.execute(
                "INSERT INTO delivery_budgets(budget_id,snapshot_json) VALUES(?,?)",
                ("crew:corrupt", "{not-json"),
            )
        with self.assertRaises(ValueError):
            self.supervisor.create(
                "e" * 64, "f" * 32, "e" * 64, budget_id="crew:corrupt"
            )
        self.assertEqual(0, self.controller.submissions)


if __name__ == "__main__":
    unittest.main()
