"""Dashboard host wiring for caller-owned shared crew budgets."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from portable.dashboard_server import _Handler, serve
from portable.runtime_recovery import RuntimeExecutionGuard


class DashboardSharedBudgetInjectionTests(unittest.TestCase):
    def test_serve_binds_the_same_guard_and_stable_budget_id_to_delivery_host(self):
        captured = {}
        class FakeServer:
            def __init__(self, address, handler):
                captured["address"] = address
                captured["handler"] = handler
            def serve_forever(self):
                raise KeyboardInterrupt
            def server_close(self):
                captured["closed"] = True

        guard = RuntimeExecutionGuard(max_delivery_bytes=12345)
        with tempfile.TemporaryDirectory() as root, patch(
            "portable.dashboard_server.ThreadingHTTPServer", FakeServer,
        ):
            serve(
                project_root=root,
                delivery_execution_guard=guard,
                delivery_budget_id="  crew:integration-test  ",
            )
        handler = captured["handler"]
        self.assertIs(handler.delivery_execution_guard, guard)
        self.assertEqual("crew:integration-test", handler.delivery_budget_id)
        self.assertTrue(captured["closed"])


    def test_artifact_delivery_creation_receives_the_exact_shared_guard(self):
        captured = {}
        class Repository:
            def stage(self, text, *, max_chunk_bytes):
                captured["text"] = text
                captured["max_chunk_bytes"] = max_chunk_bytes
                manifest = SimpleNamespace(sha256="a" * 64, total_bytes=len(text.encode("utf-8")), total_chunks=1)
                return SimpleNamespace(artifact=SimpleNamespace(digest="b" * 64), manifest=manifest)
        class Supervisor:
            def create(self, response_id, delivery_id, digest, **kwargs):
                captured["create"] = (response_id, delivery_id, digest, kwargs)
                return "delivery-task"
            def start(self, task_id, *, artifact_verified):
                captured["start"] = (task_id, artifact_verified)
        class Journal:
            def _events(self):
                return []
            def record_response_artifact(self, task_id, **kwargs):
                captured["journal"] = (task_id, kwargs)

        guard = RuntimeExecutionGuard(max_delivery_bytes=12345)
        handler = object.__new__(_Handler)
        handler.response_repository = Repository()
        handler.delivery_supervisor = Supervisor()
        handler.progress_journal = Journal()
        handler.delivery_execution_guard = guard
        handler.delivery_budget_id = "crew:real-path"
        with patch(
            "portable.dashboard_server.record_delivery_metrics",
            return_value=None,
        ):
            stored = handler._persist_response_artifact("a" * 64, "c" * 32, b"hello")
        self.assertEqual("hello", captured["text"])
        self.assertIs(captured["create"][3]["execution_guard"], guard)
        self.assertEqual("crew:real-path", captured["create"][3]["budget_id"])
        self.assertEqual(("delivery-task", True), captured["start"])
        self.assertEqual("b" * 64, stored.artifact.digest)

    def test_shared_guard_requires_stable_budget_identity(self):
        with self.assertRaisesRegex(ValueError, "stable delivery_budget_id"):
            serve(
                delivery_execution_guard=RuntimeExecutionGuard(),
                delivery_budget_id="",
            )

    def test_budget_id_can_resume_a_previously_persisted_shared_budget(self):
        captured = {}
        class FakeServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
            def serve_forever(self):
                raise KeyboardInterrupt
            def server_close(self):
                pass

        with tempfile.TemporaryDirectory() as root, patch(
            "portable.dashboard_server.ThreadingHTTPServer", FakeServer,
        ):
            serve(project_root=root, delivery_budget_id="crew:restored")
        self.assertIsNone(captured["handler"].delivery_execution_guard)
        self.assertEqual("crew:restored", captured["handler"].delivery_budget_id)


if __name__ == "__main__":
    unittest.main()
