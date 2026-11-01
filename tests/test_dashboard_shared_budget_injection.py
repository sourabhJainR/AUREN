"""Dashboard host wiring for caller-owned shared crew budgets."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from portable.dashboard_server import serve
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
