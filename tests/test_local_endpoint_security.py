"""Regression tests for local endpoint trust boundaries and routing metrics."""
import time
import unittest
from urllib.request import Request

from portable.local_endpoint import (
    _NoRedirectHandler,
    validate_loopback_base_url,
)
from portable.hybrid_model_router import HybridModelRouter, TaskRouteRequest
from portable.muxin_session_adapter import MuxinSessionAdapter
from portable.session_state import SessionCheckpoint


class _Discovery:
    def __init__(self, names):
        self.names = names
        self.base_url = "http://127.0.0.1:11434"

    def discover(self):
        return {"available": bool(self.names), "models": [{"name": name} for name in self.names]}


class LocalEndpointSecurityTests(unittest.TestCase):
    def test_accepts_only_explicit_loopback_origins(self):
        self.assertEqual("http://127.0.0.1:11434", validate_loopback_base_url("http://127.0.0.1:11434"))
        self.assertEqual("http://localhost:11434", validate_loopback_base_url("http://localhost:11434"))
        self.assertEqual("http://[::1]:11434", validate_loopback_base_url("http://[::1]:11434"))

    def test_rejects_ambiguous_or_remote_endpoint_urls(self):
        invalid = (
            "https://127.0.0.1:11434",
            "http://127.0.0.1.attacker.invalid:11434",
            "http://127.0.0.1@attacker.invalid:11434",
            "http://user:pass@127.0.0.1:11434",
            "http://2130706433:11434",
            "http://127.0.0.1",
            "http://127.0.0.1:11434/api",
            "http://127.0.0.1:11434/?next=http://attacker.invalid",
            "http://127.0.0.1:99999",
            " http://127.0.0.1:11434",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_loopback_base_url(value)

    def test_redirect_handler_refuses_redirects(self):
        handler = _NoRedirectHandler()
        request = Request("http://127.0.0.1:11434/api/tags")
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "http://example.invalid/collect"
        ))

    def test_unknown_checkpoint_stage_is_not_reported_as_running(self):
        checkpoint = SessionCheckpoint(
            session_id="session-1", task_id="task-1", project_key="project", stage="future-stage"
        )
        record = MuxinSessionAdapter._session_record(checkpoint)
        self.assertEqual("unverified", record["status"])
        self.assertEqual(0, record["progress"])

    def test_local_attempt_count_reports_actual_attempts(self):
        attempted = []

        def local_executor(request, model):
            attempted.append(model)
            if len(attempted) == 1:
                raise RuntimeError("first model unavailable")
            return "local answer"

        router = HybridModelRouter(
            discovery=_Discovery(["qwen2.5-coder:7b", "deepseek-r1:8b"]),
            local_executor=local_executor,
            remote_executor=lambda request: self.fail("private task must not use remote"),
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest(
            "private source", "coding", privacy="private", max_attempts=2
        ))
        self.assertTrue(result.success)
        self.assertEqual(2, result.attempts)
        self.assertEqual("deepseek-r1:8b", result.model)
        self.assertEqual(2, len(attempted))

    def test_local_and_remote_fallback_share_one_attempt_budget(self):
        local_calls = []
        remote_calls = []

        def fail_local(request, model):
            local_calls.append(model)
            raise RuntimeError("local model unavailable")

        router = HybridModelRouter(
            discovery=_Discovery(["general-model"]),
            local_executor=fail_local,
            remote_executor=lambda request: remote_calls.append(request) or "remote answer",
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest(
            "public summary", "general", privacy="public", max_attempts=2
        ))
        self.assertTrue(result.success)
        self.assertEqual("remote", result.provider)
        self.assertEqual(2, result.attempts)
        self.assertEqual(1, len(local_calls))
        self.assertEqual(1, len(remote_calls))

    def test_late_local_result_is_not_reported_as_success(self):
        def slow_local(request, model):
            time.sleep(0.12)
            return "late answer"

        router = HybridModelRouter(
            discovery=_Discovery(["general-model"]),
            local_executor=slow_local,
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest(
            "private source", "general", privacy="private", timeout_seconds=0.1
        ))
        self.assertFalse(result.success)
        self.assertEqual(1, result.attempts)
        self.assertIn("privacy policy", result.error)
        self.assertEqual("overall timeout budget exhausted", result.fallback_reason)

    def test_local_failure_reports_only_attempts_that_ran(self):
        attempted = []

        def fail(request, model):
            attempted.append(model)
            raise RuntimeError("unavailable")

        router = HybridModelRouter(
            discovery=_Discovery(["qwen2.5-coder:7b", "deepseek-r1:8b"]),
            local_executor=fail,
            remote_executor=lambda request: self.fail("private task must not use remote"),
            resource_reader=lambda: {},
        )
        result = router.route(TaskRouteRequest(
            "private source", "coding", privacy="private", max_attempts=1
        ))
        self.assertFalse(result.success)
        self.assertEqual(1, result.attempts)
        self.assertEqual(1, len(attempted))


if __name__ == "__main__":
    unittest.main()
