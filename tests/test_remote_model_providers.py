from __future__ import annotations

import json
import unittest

from portable.remote_model_providers import (
    OpenAICompatibleProvider,
    PROVIDER_SPECS,
    ProviderSpec,
    RemoteModelResponse,
    RemoteProviderError,
    RemoteProviderPool,
)


class RemoteModelProviderTests(unittest.TestCase):
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
