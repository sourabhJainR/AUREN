from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile

from portable.agency_observability import LocalExporter, Tracer, bind_trace_context

from portable.remote_model_providers import (
    OpenAICompatibleProvider,
    PROVIDER_SPECS,
    ProviderSpec,
    RemoteModelResponse,
    RemoteProviderError,
    RemoteProviderPool,
)


class RemoteModelProviderTests(unittest.TestCase):

    def test_direct_inference_emits_privacy_safe_parented_provider_span(self):
        payload = json.dumps({
            "id": "private-request-id",
            "choices": [{"message": {"content": "private completion body"}, "finish_reason": "sk-provider-secret-token"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }).encode()
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["groq"], api_key="provider-secret-value",
            model="private-model-id", transport=lambda *_args, **_kwargs: (200, payload),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            tracer = Tracer(LocalExporter(path, enabled=True))
            trace = tracer.start("direct-inference", run_id="run-provider", task_id="task-provider")
            parent = trace.span("model-router", phase="routing")
            prompt = "private prompt content token=do-not-export"
            with bind_trace_context(trace, parent.span_id):
                result = provider.complete(prompt)
            parent.finish({"provider_call_completed": True})
            tracer.end(trace)

            self.assertEqual("private completion body", result.content)
            provider_spans = [span for span in trace.spans if span.kind == "provider"]
            self.assertEqual(1, len(provider_spans))
            span = provider_spans[0]
            self.assertEqual(parent.span_id, span.parent_id)
            self.assertEqual("provider", span.phase)
            self.assertEqual("groq", span.provider)
            self.assertEqual("private-model-id", span.model)
            self.assertEqual("ok", span.status)
            self.assertEqual("other", span.output["finish_reason"])
            self.assertEqual({"input_units": 11, "output_units": 7, "total_units": 18}, span.output["usage"])
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-provider-secret-token", serialized)
            for private_value in (
                "private prompt content", "do-not-export", "private completion body",
                "provider-secret-value", "private-request-id",
            ):
                self.assertNotIn(private_value, serialized)
            self.assertIn(result.response_digest, serialized)

    def test_direct_inference_failure_emits_error_span_without_provider_diagnostics(self):
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="provider-secret-value",
            model="model-a",
            transport=lambda *_args, **_kwargs: (429, b'{"error":"private provider diagnostic"}'),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            tracer = Tracer(LocalExporter(path, enabled=True))
            trace = tracer.start("direct-inference-failure", task_id="task-provider-error")
            parent = trace.span("model-router", phase="routing")
            with bind_trace_context(trace, parent.span_id):
                with self.assertRaises(RemoteProviderError):
                    provider.complete("private prompt")
            tracer.end(trace, status="error")
            span = [item for item in trace.spans if item.kind == "provider"][0]
            self.assertEqual("error", span.status)
            self.assertEqual("RemoteProviderError", span.terminal_reason)
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("private provider diagnostic", serialized)
            self.assertNotIn("provider-secret-value", serialized)
            self.assertNotIn("private prompt", serialized)

    def test_observability_failures_never_change_provider_result(self):
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="provider-secret-value",
            model="model-a",
            transport=lambda *_args, **_kwargs: (200, json.dumps({
                "id": "req-observability-failure",
                "output_text": "provider result survives",
                "status": "completed",
            }).encode()),
        )
        trace = Tracer().start("observability-failure")
        parent = trace.span("router", phase="routing")
        original_span = trace.span

        def broken_span(*args, **kwargs):
            span = original_span(*args, **kwargs)
            span.finish = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("telemetry unavailable"))
            return span

        trace.span = broken_span
        with bind_trace_context(trace, parent.span_id):
            result = provider.complete("public prompt")
        self.assertEqual("provider result survives", result.content)

    def test_oversized_prompt_is_rejected_without_telemetry_copy_or_dispatch(self):
        calls = []
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="provider-secret-value",
            model="model-a",
            transport=lambda *_args, **_kwargs: calls.append("dispatched") or (200, b"{}"),
        )
        trace = Tracer().start("oversized-inference")
        parent = trace.span("router", phase="routing")
        with bind_trace_context(trace, parent.span_id):
            with self.assertRaisesRegex(ValueError, "256 KiB"):
                provider.complete("x" * (256 * 1024 + 1))
        self.assertEqual([], calls)
        self.assertEqual([parent], trace.spans)

    def test_direct_inference_without_trace_context_preserves_result_and_errors(self):
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="provider-secret-value",
            model="model-a",
            transport=lambda *_args, **_kwargs: (200, json.dumps({
                "id": "req-no-trace",
                "output_text": "no trace answer",
                "status": "completed",
            }).encode()),
        )
        self.assertEqual("no trace answer", provider.complete("public prompt").content)

    def test_native_protocols_are_selected_for_provider_apis(self):
        self.assertEqual("responses", PROVIDER_SPECS["openai"].protocol)
        self.assertEqual("responses", PROVIDER_SPECS["xai"].protocol)
        self.assertEqual("anthropic", PROVIDER_SPECS["anthropic"].protocol)
        self.assertEqual("openai", PROVIDER_SPECS["groq"].protocol)
        self.assertEqual("openai", PROVIDER_SPECS["gemini"].protocol)

    def test_unregistered_provider_endpoint_is_rejected(self):
        custom = ProviderSpec(
            "custom", "https://attacker.example/v1/chat/completions",
            "attacker.example", "CUSTOM_API_KEY", "CUSTOM_MODEL",
        )
        with self.assertRaisesRegex(ValueError, "exact host allowlist"):
            OpenAICompatibleProvider(
                custom, api_key="custom-secret-value", model="custom-model",
                transport=lambda *a, **k: (200, b"{}"),
            )

    def test_vendor_catalog_uses_fixed_https_hosts_and_explicit_secret_names(self):
        self.assertGreaterEqual(len(PROVIDER_SPECS), 10)
        for name, spec in PROVIDER_SPECS.items():
            with self.subTest(provider=name):
                self.assertTrue(spec.endpoint.startswith("https://"))
                self.assertEqual(spec.host, spec.endpoint.split("/")[2])
                self.assertTrue(spec.api_key_env.endswith("_API_KEY"))
                self.assertTrue(spec.model_env.endswith("_MODEL"))

    def test_openai_compatible_response_is_normalized_without_executing_tool_calls(self):
        seen = {}

        def transport(url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return 200, json.dumps({
                "id": "req-123",
                "choices": [{
                    "message": {
                        "content": "Reviewed result",
                        "tool_calls": [{
                            "id": "call-1",
                            "function": {"name": "auren.submit_task", "arguments": "{\"task\":\"do work\"}"},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
            }).encode()

        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["groq"], api_key="test-secret-value", model="test-model", transport=transport,
        )
        result = provider.complete("public task", timeout_seconds=4)
        self.assertIsInstance(result, RemoteModelResponse)
        self.assertEqual("groq", result.provider)
        self.assertEqual("test-model", result.model)
        self.assertEqual("Reviewed result", result.content)
        self.assertEqual(19, result.usage["total_tokens"])
        self.assertEqual("call-1", result.tool_calls[0]["id"])
        self.assertEqual("auren.submit_task", result.tool_calls[0]["name"])
        self.assertEqual(("api.groq.com",), tuple(seen["allowed_hosts"]))
        self.assertEqual("Bearer test-secret-value", seen["headers"]["authorization"])
        self.assertNotIn("test-secret-value", result.response_digest)

    def test_openai_responses_api_uses_native_payload_and_normalizes_output(self):
        seen = {}

        def transport(url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return 200, json.dumps({
                "id": "resp-1",
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "Responses API answer"}]},
                    {"type": "function_call", "call_id": "call-2", "name": "auren.get_task",
                     "arguments": "{\"task_id\":\"task-1\"}"},
                ],
                "usage": {"input_tokens": 9, "output_tokens": 6, "total_tokens": 15},
            }).encode()

        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="openai-secret-value",
            model="gpt-test", transport=transport,
        )
        result = provider.complete("public task")
        payload = json.loads(seen["body"].decode())
        self.assertTrue(seen["url"].endswith("/v1/responses"))
        self.assertEqual("public task", payload["input"])
        self.assertIn("instructions", payload)
        self.assertEqual("Responses API answer", result.content)
        self.assertEqual("completed", result.finish_reason)
        self.assertEqual("auren.get_task", result.tool_calls[0]["name"])
        self.assertEqual(15, result.usage["total_tokens"])

    def test_anthropic_messages_api_has_native_headers_and_response_normalization(self):
        seen = {}

        def transport(url, **kwargs):
            seen.update(kwargs)
            return 200, json.dumps({
                "id": "msg-1",
                "content": [{"type": "text", "text": "Anthropic result"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }).encode()

        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["anthropic"], api_key="test-anthropic-secret",
            model="claude-test", transport=transport,
        )
        result = provider.complete("public task")
        self.assertEqual("Anthropic result", result.content)
        self.assertEqual("end_turn", result.finish_reason)
        self.assertEqual("test-anthropic-secret", seen["headers"]["x-api-key"])
        self.assertEqual("2023-06-01", seen["headers"]["anthropic-version"])

    def test_cohere_v2_chat_uses_fixed_host_and_normalizes_response(self):
        seen = {}
        def transport(url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return 200, json.dumps({
                "id": "cohere-req-1",
                "finish_reason": "COMPLETE",
                "message": {"content": [{"type": "text", "text": "Cohere answer"}]},
                "usage": {"billed_units": {"input_tokens": 8, "output_tokens": 5}},
            }).encode()
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["cohere"], api_key="cohere-secret-value",
            model="command-test", transport=transport,
        )
        result = provider.complete("public task")
        self.assertEqual("https://api.cohere.com/v2/chat", seen["url"])
        self.assertEqual(("api.cohere.com",), tuple(seen["allowed_hosts"]))
        self.assertEqual("Cohere answer", result.content)
        self.assertEqual(8, result.usage["input_tokens"])
        self.assertEqual(5, result.usage["output_tokens"])
        self.assertNotIn("cohere-secret-value", str(result))

    def test_http_error_is_redacted_and_classified(self):
        def transport(url, **kwargs):
            return 429, b'{"error":"private vendor diagnostic"}'

        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["xai"], api_key="test-secret-value", model="grok-test", transport=transport,
        )
        with self.assertRaises(RemoteProviderError) as caught:
            provider.complete("public task")
        self.assertEqual(429, caught.exception.status)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("test-secret-value", str(caught.exception))
        self.assertNotIn("private vendor diagnostic", str(caught.exception))

    def test_invalid_json_and_empty_completion_fail_closed(self):
        for payload in (b"not-json", b'{"choices":[]}'):
            with self.subTest(payload=payload):
                provider = OpenAICompatibleProvider(
                    PROVIDER_SPECS["openai"], api_key="test-secret-value",
                    model="test-model", transport=lambda url, **kwargs: (200, payload),
                )
                with self.assertRaises(RemoteProviderError):
                    provider.complete("public task")

    def test_provider_pool_uses_configured_order_and_attempt_index(self):
        calls = []

        def transport(url, **kwargs):
            calls.append(url)
            if "api.openai.com" in url:
                return 503, b"temporary outage"
            return 200, json.dumps({
                "id": "req-2",
                "status": "completed",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "fallback answer"}],
                }],
                "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            }).encode()

        providers = [
            OpenAICompatibleProvider(PROVIDER_SPECS["openai"], api_key="openai-secret", model="model-a", transport=transport),
            OpenAICompatibleProvider(PROVIDER_SPECS["xai"], api_key="xai-secret", model="model-b", transport=transport),
        ]
        pool = RemoteProviderPool(providers)

        class Request:
            task = "public task"
            timeout_seconds = 5
            provider_attempt = 0

        with self.assertRaises(RemoteProviderError):
            pool(Request())
        Request.provider_attempt = 1
        result = pool(Request())
        self.assertEqual("xai", result.provider)
        self.assertEqual("fallback answer", result.content)
        self.assertEqual(2, len(calls))

    def test_pool_honors_per_request_provider_order_and_attempt_budget(self):
        calls = []

        def transport(url, **kwargs):
            calls.append(url)
            return 200, json.dumps({
                "id": "req-selected",
                "status": "completed",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "selected provider"}],
                }],
            }).encode()

        pool = RemoteProviderPool([
            OpenAICompatibleProvider(PROVIDER_SPECS["openai"], api_key="openai-secret", model="model-a", transport=transport),
            OpenAICompatibleProvider(PROVIDER_SPECS["xai"], api_key="xai-secret", model="model-b", transport=transport),
        ])

        class Request:
            task = "public task"
            timeout_seconds = 5
            provider_attempt = 0
            provider_order = ("xai",)

        self.assertEqual(1, pool.max_attempts_for(Request()))
        result = pool(Request())
        self.assertEqual("xai", result.provider)
        self.assertTrue(calls[0].startswith("https://api.x.ai/"))
        Request.provider_attempt = 1
        with self.assertRaisesRegex(RemoteProviderError, "chain is exhausted"):
            pool(Request())
        Request.provider_attempt = 0
        Request.provider_order = ("not-configured",)
        with self.assertRaisesRegex(RemoteProviderError, "not configured"):
            pool(Request())

    def test_provider_configuration_report_is_secret_safe_and_supports_multiple_providers(self):
        from portable.remote_model_providers import provider_configuration_report

        env = {
            "AUREN_REMOTE_PROVIDER_ORDER": "groq,openai",
            "GROQ_API_KEY": "groq-super-secret",
            "GROQ_MODEL": "llama-current",
            "OPENAI_API_KEY": "openai-super-secret",
            "OPENAI_MODEL": "gpt-current",
        }
        report = provider_configuration_report(environ=env)
        self.assertEqual("configured_not_live_validated", report["status"])
        self.assertEqual(["groq", "openai"], report["provider_order"])
        self.assertEqual(["configured_not_live_validated"] * 2, [item["status"] for item in report["providers"]])
        serialized = json.dumps(report)
        self.assertNotIn("groq-super-secret", serialized)
        self.assertNotIn("openai-super-secret", serialized)

    def test_provider_configuration_report_marks_missing_and_invalid_configuration(self):
        from portable.remote_model_providers import provider_configuration_report

        report = provider_configuration_report(environ={
            "AUREN_REMOTE_PROVIDER_ORDER": "groq,custom,groq",
            "GROQ_API_KEY": "groq-super-secret",
            "AUREN_CUSTOM_PROVIDER_URL": "https://models.example.net:bad/v1/chat/completions",
        })
        self.assertEqual("incomplete", report["status"])
        self.assertIn("provider_order_contains_duplicates", report["errors"])
        self.assertEqual("incomplete", report["providers"][0]["status"])
        self.assertIn("model_id", report["providers"][0]["missing"])
        self.assertIn("valid_allowlisted_endpoint", report["providers"][1]["missing"])
        self.assertNotIn("groq-super-secret", json.dumps(report))

    def test_provider_configuration_report_disables_remote_inference_without_order(self):
        from portable.remote_model_providers import provider_configuration_report

        report = provider_configuration_report(environ={
            "OPENAI_API_KEY": "configured-but-not-enabled",
            "OPENAI_MODEL": "gpt-current",
        })
        self.assertFalse(report["enabled"])
        self.assertEqual("disabled", report["status"])
        self.assertEqual([], report["providers"])

    def test_environment_factory_requires_explicit_order_key_and_model(self):
        env = {
            "AUREN_REMOTE_PROVIDER_ORDER": "groq,xai",
            "GROQ_API_KEY": "groq-secret-value",
            "GROQ_MODEL": "llama-test",
            "XAI_API_KEY": "xai-secret-value",
        }
        pool = RemoteProviderPool.from_environment(environ=env, transport=lambda *a, **k: (500, b""))
        self.assertEqual(["groq"], [provider.spec.name for provider in pool.providers])
        with self.assertRaisesRegex(ValueError, "unsupported providers"):
            RemoteProviderPool.from_environment(environ={"AUREN_REMOTE_PROVIDER_ORDER": "unknown"})

    def test_custom_openai_compatible_provider_requires_explicit_host_allowlist(self):
        env = {
            "AUREN_REMOTE_PROVIDER_ORDER": "custom",
            "AUREN_CUSTOM_PROVIDER_URL": "https://models.example.net/v1/chat/completions",
            "AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS": "models.example.net",
            "AUREN_CUSTOM_PROVIDER_API_KEY": "custom-provider-secret",
            "AUREN_CUSTOM_PROVIDER_MODEL": "custom-model",
        }
        pool = RemoteProviderPool.from_environment(
            environ=env, transport=lambda *a, **k: (503, b""),
        )
        self.assertEqual(["custom"], [provider.spec.name for provider in pool.providers])
        self.assertEqual("models.example.net", pool.providers[0].spec.host)
        env["AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS"] = "other.example.net"
        with self.assertRaisesRegex(ValueError, "explicitly allowlisted"):
            RemoteProviderPool.from_environment(
                environ=env, transport=lambda *a, **k: (503, b""),
            )

    def test_provider_request_bounds_prompt_and_timeout(self):
        provider = OpenAICompatibleProvider(
            PROVIDER_SPECS["openai"], api_key="test-secret-value", model="test-model",
            transport=lambda *a, **k: (200, b'{"choices":[{"message":{"content":"ok"}}]}'),
        )
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            provider.complete("x" * (256 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "timeout"):
            provider.complete("public task", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
