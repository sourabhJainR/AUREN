"""Hybrid routing policy and bounded fallback regression tests."""
import unittest
from portable.hybrid_model_router import HybridModelRouter, TaskRouteRequest
from portable.runtime_recovery import RuntimeExecutionGuard

class Discovery:
    def __init__(self, names):
        self.names = names
        self.calls = 0

    def discover(self):
        self.calls += 1
        return {"available": bool(self.names), "models": [{"name": n} for n in self.names]}

class HybridRouterTests(unittest.TestCase):
    def test_private_task_never_falls_back_to_remote(self):
        remote_calls = []
        router = HybridModelRouter(discovery=Discovery([]),
            remote_executor=lambda request: remote_calls.append(request) or "remote")
        result = router.route(TaskRouteRequest("private source", "coding", privacy="private"))
        self.assertFalse(result.success)
        self.assertEqual([], remote_calls)
        self.assertIn("privacy policy", result.error)

    def test_public_task_uses_explicit_remote_executor(self):
        router = HybridModelRouter(discovery=Discovery([]),
            remote_executor=lambda request: "ok",
            resource_reader=lambda: {"rss_bytes": 10})
        result = router.route(TaskRouteRequest("public summary", "general", privacy="public"))
        self.assertTrue(result.success)
        self.assertEqual("remote", result.provider)
        self.assertEqual("ok", result.result)
        self.assertIn("before", result.resource_usage)

    def test_local_capability_route_and_failure_is_bounded(self):
        calls = []
        def fail(request, model):
            calls.append(model)
            raise RuntimeError("offline")
        router = HybridModelRouter(discovery=Discovery(["qwen2.5-coder:7b", "deepseek-r1"]),
            local_executor=fail, remote_executor=lambda request: "must-not-run",
            resource_reader=lambda: {})
        result = router.route(TaskRouteRequest("private code", "coding", privacy="restricted", max_attempts=1))
        self.assertFalse(result.success)
        self.assertEqual(1, len(calls))
        self.assertEqual("local", result.provider)
        self.assertIn("privacy policy", result.error)

    def test_route_with_context_reuses_graph_guard_across_provider_calls(self):
        calls = []
        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=1)
        context = {"_auren_runtime_execution_guard": guard}
        discovery = Discovery([])
        router = HybridModelRouter(
            discovery=discovery,
            remote_executor=lambda request: calls.append(request) or "ok",
            resource_reader=lambda: {},
        )

        first = router.route_with_context(
            TaskRouteRequest("public summary", "general", privacy="public", max_attempts=1),
            context,
        )
        second = router.route_with_context(
            TaskRouteRequest("another public summary", "general", privacy="public", max_attempts=1),
            context,
        )

        self.assertTrue(first.success)
        self.assertFalse(second.success)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, guard.attempts_used)
        self.assertEqual(1, discovery.calls, "exhausted shared budget must block before discovery")

    def test_route_with_context_rejects_forged_guard_before_discovery(self):
        discovery = Discovery([])
        router = HybridModelRouter(discovery=discovery, resource_reader=lambda: {})
        with self.assertRaisesRegex(TypeError, "invalid execution guard"):
            router.route_with_context(
                TaskRouteRequest("public summary", "general", privacy="public"),
                {"_auren_runtime_execution_guard": object()},
            )

    def test_attempts_are_clamped_to_three(self):
        calls = []
        router = HybridModelRouter(discovery=Discovery([]),
            remote_executor=lambda request: (calls.append(1), (_ for _ in ()).throw(RuntimeError()))[1],
            resource_reader=lambda: {})
        result = router.route(TaskRouteRequest("public task", "general", privacy="public", max_attempts=99))
        self.assertEqual(3, result.attempts)
        self.assertEqual(3, len(calls))

if __name__ == "__main__":
    unittest.main()
