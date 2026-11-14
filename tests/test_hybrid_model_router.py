"""Hybrid routing policy and bounded fallback regression tests."""
import time
import unittest
import tempfile
from pathlib import Path
from threading import Event, Thread
from portable.hybrid_model_router import HybridModelRouter, TaskRouteRequest
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.remote_model_providers import RemoteModelResponse, RemoteProviderError
from portable.agency_observability import LocalExporter, Tracer, bind_trace_context

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

    def test_explicit_provider_order_is_honored_before_local_discovery(self):
        discovery = Discovery(["qwen2.5-coder:7b"])
        calls = []

        class SelectedProviderPool:
            max_attempts = 3

            def max_attempts_for(self, request):
                self.request = request
                return len(request.provider_order)

            def __call__(self, request):
                calls.append(request)
                return RemoteModelResponse(
                    provider=request.provider_order[request.provider_attempt],
                    model="selected-model", content="selected result",
                    usage={}, request_id=None, finish_reason="stop",
                    latency_ms=1.0, response_digest="a" * 64,
                )

        pool = SelectedProviderPool()
        router = HybridModelRouter(
            discovery=discovery, remote_executor=pool, resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest(
            "public task", "general", privacy="public", max_attempts=3,
            provider_order=("xai", "groq"),
        ))
        self.assertTrue(result.success)
        self.assertEqual("xai", result.provider)
        self.assertEqual(0, discovery.calls)
        self.assertEqual(("xai", "groq"), calls[0].provider_order)
        self.assertEqual(2, pool.max_attempts_for(pool.request))

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

    def test_explicit_capability_metadata_outweighs_name_heuristics(self):
        class MetadataDiscovery:
            def discover(self):
                return {"available": True, "models": [
                    {"name": "z-coder-model"},
                    {"name": "general-model", "capabilities": ["coding", "reasoning"]},
                    {"name": "a-coder-model", "capabilities": ["general"]},
                    {"name": "malformed", "capabilities": "coding"},
                ]}
        calls = []
        router = HybridModelRouter(
            discovery=MetadataDiscovery(),
            local_executor=lambda request, model: calls.append(model) or "ok",
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest("private code", "coding", privacy="private", max_attempts=1))
        self.assertTrue(result.success)
        self.assertEqual("general-model", result.model)
        self.assertEqual(["general-model"], calls)

    def test_explicit_capability_precedes_heuristics_and_preserves_provider_priority(self):
        models = [
            {"name": "z-coder"},
            {"name": "a-coder"},
            {"name": "general", "capabilities": ["coding"]},
        ]
        self.assertEqual(
            ["general", "z-coder", "a-coder"],
            HybridModelRouter._ranked_candidates(models, "coding"),
        )

    def test_discovery_exception_fails_closed_for_private_task(self):
        class BrokenDiscovery:
            def discover(self):
                raise OSError("endpoint unavailable")
        remote_calls = []
        router = HybridModelRouter(
            discovery=BrokenDiscovery(),
            remote_executor=lambda request: remote_calls.append(request) or "unsafe",
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest("private code", "coding", privacy="restricted"))
        self.assertFalse(result.success)
        self.assertEqual([], remote_calls)
        self.assertIn("discovery failed", result.fallback_reason)

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

    def test_provider_dispatch_waits_for_guard_checkpoint(self):
        events = []
        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=2)
        router = HybridModelRouter(
            discovery=Discovery([]),
            remote_executor=lambda request: events.append("provider") or "ok",
            resource_reader=lambda: {},
        )

        result = router.route(
            TaskRouteRequest("public summary", "general", privacy="public", max_attempts=1),
            execution_guard=guard,
            execution_guard_persist=lambda snapshot: events.append(("checkpoint", snapshot["attempts_used"])),
        )

        self.assertTrue(result.success)
        self.assertEqual(("checkpoint", 1), events[0])
        self.assertEqual("provider", events[1])

    def test_failed_guard_checkpoint_prevents_provider_dispatch(self):
        calls = []
        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=2)
        router = HybridModelRouter(
            discovery=Discovery([]),
            remote_executor=lambda request: calls.append(request) or "should not run",
            resource_reader=lambda: {},
        )

        def fail_checkpoint(_snapshot):
            raise OSError("checkpoint store unavailable")

        result = router.route(
            TaskRouteRequest("public summary", "general", privacy="public", max_attempts=1),
            execution_guard=guard,
            execution_guard_persist=fail_checkpoint,
        )

        self.assertFalse(result.success)
        self.assertEqual([], calls)
        self.assertEqual(1, guard.attempts_used)

    def test_route_with_context_rejects_forged_guard_before_discovery(self):
        discovery = Discovery([])
        router = HybridModelRouter(discovery=discovery, resource_reader=lambda: {})
        with self.assertRaisesRegex(TypeError, "invalid execution guard"):
            router.route_with_context(
                TaskRouteRequest("public summary", "general", privacy="public"),
                {"_auren_runtime_execution_guard": object()},
            )

    def test_remote_provider_failover_uses_supervisor_attempt_charges_and_returns_provider_identity(self):
        calls = []

        class ProviderPool:
            def __call__(self, request):
                calls.append(request.provider_attempt)
                if request.provider_attempt == 0:
                    raise RemoteProviderError("openai", "temporary outage", status=503)
                return RemoteModelResponse(
                    provider="groq", model="model-b", content="answer",
                    usage={"total_tokens": 12}, request_id="req-2",
                    finish_reason="stop", latency_ms=25.0,
                    response_digest="d" * 64,
                )

        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=2)
        router = HybridModelRouter(
            discovery=Discovery([]), remote_executor=ProviderPool(), resource_reader=lambda: {},
        )
        result = router.route(
            TaskRouteRequest("public summary", "general", privacy="public", max_attempts=2),
            execution_guard=guard,
        )
        self.assertTrue(result.success)
        self.assertEqual("groq", result.provider)
        self.assertEqual("model-b", result.model)
        self.assertEqual("answer", result.result)
        self.assertEqual([0, 1], calls)
        self.assertEqual(2, guard.attempts_used)
        self.assertEqual(12, result.provider_metadata["usage"]["total_tokens"])
        self.assertEqual("req-2", result.provider_metadata["request_id"])

    def test_route_emits_parented_privacy_safe_decision_span(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "traces.jsonl"
            tracer = Tracer(LocalExporter(trace_path, enabled=True))
            trace = tracer.start(
                "supervised-model-task",
                run_id="run-route-observation",
                task_id="task-route-observation",
            )
            supervisor = trace.span("supervisor", phase="supervisor")
            router = HybridModelRouter(
                discovery=Discovery([]),
                remote_executor=lambda request: RemoteModelResponse(
                    provider="groq",
                    model="pinned-model-v1",
                    content="answer that must not enter telemetry",
                    usage={"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
                    request_id="provider-request-1",
                    finish_reason="stop",
                    latency_ms=17.5,
                    response_digest="b" * 64,
                ),
                resource_reader=lambda: {},
            )
            private_prompt = "public task payload that must not be persisted"
            with bind_trace_context(trace, supervisor.span_id):
                result = router.route(TaskRouteRequest(
                    private_prompt, "general", privacy="public", max_attempts=2,
                    provider_order=("groq",),
                ))
            supervisor.finish({"dispatch_completed": True})
            tracer.end(trace)

            self.assertTrue(result.success)
            route_spans = [span for span in trace.spans if span.name == "model.route"]
            self.assertEqual(1, len(route_spans))
            route_span = route_spans[0]
            self.assertEqual(supervisor.span_id, route_span.parent_id)
            self.assertEqual("routing", route_span.phase)
            self.assertEqual("ok", route_span.status)
            self.assertEqual("groq", route_span.output["provider"])
            self.assertEqual("pinned-model-v1", route_span.output["model"])
            self.assertEqual(13, route_span.output["usage"]["total_tokens"])
            serialized = trace_path.read_text(encoding="utf-8")
            self.assertNotIn(private_prompt, serialized)
            self.assertNotIn("answer that must not enter telemetry", serialized)
            self.assertNotIn("provider-request-1", serialized)

    def test_route_observability_failure_does_not_change_route_result(self):
        class BrokenTrace:
            def span(self, *_args, **_kwargs):
                raise OSError("telemetry unavailable")

        router = HybridModelRouter(
            discovery=Discovery([]),
            remote_executor=lambda request: "completed",
            resource_reader=lambda: {},
        )
        from portable.agency_observability import bind_trace_context
        # A valid Trace is required by the context binder, so use a Tracer whose
        # exporter fails and confirm routing still returns its provider result.
        class BrokenExporter:
            enabled = True

            def export(self, _trace):
                raise OSError("telemetry unavailable")

            def export_event(self, *_args):
                raise OSError("telemetry unavailable")

        tracer = Tracer(BrokenExporter())
        trace = tracer.start("broken-export")
        with bind_trace_context(trace):
            result = router.route(TaskRouteRequest(
                "public task", "general", privacy="public", max_attempts=1,
            ))
        self.assertTrue(result.success)
        self.assertEqual("completed", result.result)

    def test_attempts_are_clamped_to_three(self):
        calls = []
        router = HybridModelRouter(discovery=Discovery([]),
            remote_executor=lambda request: (calls.append(1), (_ for _ in ()).throw(RuntimeError()))[1],
            resource_reader=lambda: {})
        result = router.route(TaskRouteRequest("public task", "general", privacy="public", max_attempts=99))
        self.assertEqual(3, result.attempts)
        self.assertEqual(3, len(calls))

    def test_route_supervised_rejects_expired_deadline_before_discovery(self):
        discovery = Discovery(["qwen2.5-coder:7b"])
        local_calls = []
        router = HybridModelRouter(
            discovery=discovery,
            local_executor=lambda request, model: local_calls.append(model) or "late",
            resource_reader=lambda: {},
        )
        with self.assertRaisesRegex(ExecutionBudgetExceeded, "deadline expired before model routing"):
            router.route_supervised(
                TaskRouteRequest("private code", "coding"),
                execution_payload={"auren_deadline_epoch": time.time() - 1},
            )
        self.assertEqual(0, discovery.calls)
        self.assertEqual([], local_calls)

    def test_route_supervised_propagates_preexisting_cancellation_before_discovery(self):
        cancellation = Event()
        cancellation.set()
        discovery = Discovery([])
        remote_calls = []
        router = HybridModelRouter(
            discovery=discovery,
            remote_executor=lambda request: remote_calls.append(request) or "unsafe",
            resource_reader=lambda: {},
        )
        result = router.route_supervised(
            TaskRouteRequest("public summary", "general", privacy="public"),
            execution_payload={"auren_cancel_event": cancellation},
        )
        self.assertFalse(result.success)
        self.assertEqual(0, discovery.calls)
        self.assertEqual([], remote_calls)

    def test_route_supervised_caps_timeout_to_absolute_task_deadline(self):
        requests = []
        router = HybridModelRouter(
            discovery=Discovery([]),
            remote_executor=lambda request: requests.append(request) or "ok",
            resource_reader=lambda: {},
        )
        result = router.route_supervised(
            TaskRouteRequest("public summary", "general", privacy="public", timeout_seconds=5),
            execution_payload={"auren_deadline_epoch": time.time() + 0.5},
        )
        self.assertTrue(result.success)
        self.assertEqual(1, len(requests))
        self.assertLessEqual(requests[0].timeout_seconds, 0.5)

    def test_route_supervised_rejects_provider_result_after_cancellation(self):
        cancellation = Event()
        started = Event()
        def remote_executor(request):
            started.set()
            cancellation.wait(timeout=4)
            return "late provider result"
        router = HybridModelRouter(
            discovery=Discovery([]), remote_executor=remote_executor, resource_reader=lambda: {},
        )
        guard = RuntimeExecutionGuard(max_steps=3, max_attempts=3, cancellation_event=cancellation)
        results = []
        worker = Thread(target=lambda: results.append(router.route_supervised(
            TaskRouteRequest("public summary", "general", privacy="public", timeout_seconds=5),
            execution_payload={"auren_cancel_event": cancellation},
            execution_guard=guard,
        )), daemon=True)
        worker.start()
        self.assertTrue(started.wait(timeout=3), "provider did not start")
        cancellation.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(results))
        self.assertFalse(results[0].success)
        self.assertTrue(guard.cancellation_event.is_set())

if __name__ == "__main__":
    unittest.main()
