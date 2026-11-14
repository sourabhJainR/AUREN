from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import provider_conformance
from portable.remote_model_providers import PROVIDER_SPECS, RemoteModelResponse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provider_conformance.py"
MATRIX = ROOT / ".ai-harness" / "PROVIDER_MATRIX.json"


class ProviderConformanceTests(unittest.TestCase):
    def test_required_providers_and_contract(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.assertTrue({"claude", "codex", "gemini", "chatgpt"}.issubset(matrix["providers"]))
        expected = {
            "intent_digest", "goal", "boundaries", "acceptance", "risk",
            "capability_plan", "context_lease_digests", "tool_observations",
            "verification_evidence", "outcome",
        }
        self.assertTrue(expected.issubset(matrix["normalized_contract"]))

    def test_platform_integration_matrix_covers_requested_hosts_without_claiming_live_validation(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        integrations = matrix.get("platform_integrations", {})
        required = {
            "chatgpt_web", "chatgpt_desktop_codex", "codex_cli", "codex_app",
            "claude_chat_web", "claude_code_cli", "claude_desktop", "claude_vscode",
            "visual_studio_ide", "gemini_cli", "gemini_code_assist_vscode",
            "cursor", "groq_api", "grok_chat_web", "xai_api", "grok_build_cli",
            "github_copilot",
        }
        self.assertTrue(required.issubset(integrations))
        for name in required:
            with self.subTest(platform=name):
                entry = integrations[name]
                self.assertIn(entry.get("support"), {
                    "repository_configured", "partial", "chat_only", "not_wired", "not_native",
                })
                self.assertTrue(entry.get("kind"))
                self.assertTrue(entry.get("mechanisms"))
                self.assertTrue(entry.get("next_steps"))
                self.assertFalse(entry.get("live_verified", False))

    def test_mcp_client_json_templates_are_valid_and_contain_no_credentials(self):
        templates = [
            "gemini.settings.json.example", "cursor.mcp.json.example",
            "vscode.mcp.json.example", "cline.mcp.json.example",
            "roo.mcp.json.example", "zed.settings.json.example",
        ]
        base = ROOT / "integrations" / "mcp"
        for name in templates:
            with self.subTest(template=name):
                payload = json.loads((base / name).read_text(encoding="utf-8"))
                self.assertTrue(payload)
                self.assertNotIn("Bearer ", json.dumps(payload))
                self.assertNotIn("api_key", json.dumps(payload).lower())
        codex = (base / "codex.config.toml.example").read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.auren]", codex)
        self.assertIn("https://mcp.example.com/mcp", codex)

    def test_orchestration_frameworks_have_first_party_connection_guides(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        integrations = matrix.get("platform_integrations", {})
        required = {"openai_agents_sdk", "autogen", "pydantic_ai", "langchain_langgraph", "n8n"}
        self.assertTrue(required.issubset(integrations))
        for name in required:
            with self.subTest(framework=name):
                entry = integrations[name]
                self.assertEqual("orchestration_framework", entry["kind"])
                self.assertFalse(entry.get("live_verified", False))
                self.assertTrue(entry["repository_artifacts"])
                self.assertTrue(entry["next_steps"])
        guide = (ROOT / "docs" / "ORCHESTRATOR_INTEGRATIONS.md").read_text(encoding="utf-8")
        for name in ("OpenAI Agents SDK", "AutoGen", "Pydantic AI", "LangChain and LangGraph", "n8n"):
            with self.subTest(guide=name):
                self.assertIn(name, guide)

    def test_machine_provider_catalog_matches_direct_adapter_registry(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        adapters = matrix["llm_provider_adapters"]["direct_http_api_adapters"]
        expected = {
            "openai_responses", "anthropic_messages", "gemini_openai_compatible",
            "groq_chat_completions", "xai_responses", "openrouter", "together",
            "fireworks", "deepseek", "mistral", "cerebras", "perplexity",
            "custom_openai_compatible",
        }
        self.assertTrue(expected.issubset(set(adapters)))
        self.assertNotIn("no_groq_or_xai_provider_adapter", matrix["remote_mcp"]["known_limits"])

    def test_custom_provider_is_allowlisted_and_documented(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        entry = matrix["platform_integrations"]["custom_openai_compatible_api"]
        self.assertEqual("partial", entry["support"])
        self.assertFalse(entry.get("live_verified", False))
        doc = (ROOT / "docs" / "MODEL_PROVIDER_INTEGRATION.md").read_text(encoding="utf-8")
        self.assertIn("AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS", doc)
        self.assertIn("port 443", doc)

    def test_mcp_host_compatibility_docs_cover_major_ide_clients(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        integrations = matrix.get("platform_integrations", {})
        required = {
            "cline_ide", "roo_code_ide", "continue_ide", "zed_ide",
            "openhands_local", "windsurf_devin_local", "visual_studio_ide",
        }
        self.assertTrue(required.issubset(integrations))
        doc = (ROOT / "docs" / "MCP_HOST_COMPATIBILITY.md").read_text(encoding="utf-8")
        for host in ("ChatGPT web", "Codex CLI", "Claude Code", "Gemini CLI",
                     "Cursor", "Visual Studio", "Cline", "Roo Code", "Continue", "Zed"):
            with self.subTest(host=host):
                self.assertIn(host, doc)
        self.assertIn("live_verified=false", doc)
        self.assertIn("does not implement the MCP Tasks extension", doc)

    def test_direct_api_provider_catalog_matches_machine_readable_matrix(self):
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        integrations = matrix.get("platform_integrations", {})
        for name, spec in PROVIDER_SPECS.items():
            key = name + "_api"
            with self.subTest(provider=name):
                self.assertIn(key, integrations)
                entry = integrations[key]
                self.assertEqual("partial", entry["support"])
                self.assertFalse(entry.get("live_verified", False))
                self.assertIn("portable/remote_model_providers.py", entry["repository_artifacts"])
                self.assertTrue(spec.endpoint.startswith("https://"))
                self.assertEqual(spec.host, spec.endpoint.split("/")[2])

    def test_mcp_setup_docs_point_to_executable_stdio_server(self):
        usage = (ROOT / "docs" / "USAGE_AND_PLATFORM_INTEGRATION.md").read_text(encoding="utf-8")
        matrix_doc = (ROOT / "docs" / "PLATFORM_COMPATIBILITY_MATRIX.md").read_text(encoding="utf-8")
        cursor_rule = ROOT / ".cursor" / "rules" / "auren.mdc"
        self.assertIn("portable/chat_capability.py", usage)
        self.assertNotIn("portable/agency_mcp.py", usage)
        self.assertTrue(cursor_rule.is_file())
        self.assertIn("AGENTS.md", cursor_rule.read_text(encoding="utf-8"))
        self.assertIn("GROQ_API_KEY", matrix_doc)
        self.assertIn("XAI_API_KEY", matrix_doc)

    def test_direct_api_probe_requires_explicit_public_remote_policy(self):
        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "openai",
            "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
            "OPENAI_API_KEY": "test-provider-secret",
            "OPENAI_MODEL": "pinned-test-model",
        }
        with patch(
            "portable.remote_model_providers.OpenAICompatibleProvider.from_environment",
            side_effect=AssertionError("blocked probe must not construct a provider"),
        ) as factory:
            result = provider_conformance.live_api_probe(
                "openai", environ=environment,
            )
        self.assertEqual("blocked", result.status)
        self.assertEqual("remote inference opt-in is not enabled", result.detail)
        factory.assert_not_called()

    def test_direct_api_probe_records_safe_acceptance_evidence(self):
        class FakeProvider:
            model = "pinned-test-model"

            def complete(self, prompt, *, timeout_seconds):
                self.prompt = prompt
                self.timeout_seconds = timeout_seconds
                return RemoteModelResponse(
                    provider="openai",
                    model=self.model,
                    content="AUREN_PROVIDER_PROBE_OK",
                    usage={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
                    request_id="provider-request-id",
                    finish_reason="stop",
                    latency_ms=25.5,
                    response_digest="a" * 64,
                )

        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "openai",
            "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
            "AUREN_LOCAL_DATA_CLASSIFICATION": "private",
            "OPENAI_API_KEY": "test-provider-secret",
            "OPENAI_MODEL": "pinned-test-model",
        }
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "probe-traces.jsonl"
            with patch(
                "portable.remote_model_providers.OpenAICompatibleProvider.from_environment",
                return_value=provider,
            ):
                result = provider_conformance.live_api_probe(
                    "openai", environ=environment, trace_path=trace_path,
                )
            trace_text = trace_path.read_text(encoding="utf-8")

        self.assertEqual("pass", result.status)
        self.assertTrue(result.evidence["sentinel_match"])
        self.assertEqual("pinned-test-model", result.evidence["model"])
        self.assertEqual(16, result.evidence["usage"]["total_tokens"])
        self.assertEqual(25.5, result.evidence["latency_ms"])
        self.assertEqual(64, len(result.evidence["prompt_digest"]))
        self.assertNotIn("test-provider-secret", json.dumps(result.evidence))
        self.assertNotIn(provider.prompt, trace_text)
        self.assertNotIn("AUREN_PROVIDER_PROBE_OK", trace_text)
        self.assertNotIn("provider-request-id", trace_text)
        self.assertIn("provider.api_acceptance_probe", trace_text)
        self.assertIn("total_units", trace_text)
        self.assertIn('"total_units":16', trace_text)

    def test_direct_api_probe_fails_without_exposing_provider_error_or_output(self):
        class BrokenProvider:
            model = "pinned-test-model"

            def complete(self, prompt, *, timeout_seconds):
                raise RuntimeError("provider response contains secret material")

        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "openai",
            "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
            "AUREN_LOCAL_DATA_CLASSIFICATION": "non_sensitive",
            "OPENAI_API_KEY": "test-provider-secret",
            "OPENAI_MODEL": "pinned-test-model",
        }
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "probe-traces.jsonl"
            with patch(
                "portable.remote_model_providers.OpenAICompatibleProvider.from_environment",
                return_value=BrokenProvider(),
            ):
                result = provider_conformance.live_api_probe(
                    "openai", environ=environment, trace_path=trace_path,
                )
            trace_text = trace_path.read_text(encoding="utf-8")

        self.assertEqual("fail", result.status)
        self.assertEqual("RuntimeError", result.evidence["error_type"])
        self.assertNotIn("secret material", json.dumps(result.evidence))
        self.assertNotIn("test-provider-secret", trace_text)
        self.assertNotIn("secret material", trace_text)

    def test_direct_api_probe_rejects_unexpected_model_response(self):
        class FakeProvider:
            model = "pinned-test-model"

            def complete(self, prompt, *, timeout_seconds):
                return RemoteModelResponse(
                    provider="openai", model=self.model, content="unexpected output",
                    usage={}, request_id=None, finish_reason="stop",
                    latency_ms=5.0, response_digest="c" * 64,
                )

        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "openai",
            "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
            "OPENAI_API_KEY": "test-provider-secret",
            "OPENAI_MODEL": "pinned-test-model",
        }
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "probe-traces.jsonl"
            with patch(
                "portable.remote_model_providers.OpenAICompatibleProvider.from_environment",
                return_value=FakeProvider(),
            ):
                result = provider_conformance.live_api_probe(
                    "openai", environ=environment, trace_path=trace_path,
                )
            trace_text = trace_path.read_text(encoding="utf-8")
        self.assertEqual("fail", result.status)
        self.assertFalse(result.evidence["sentinel_match"])
        self.assertNotIn("unexpected output", trace_text)

    def test_run_reports_selected_api_probe_without_claiming_full_release_readiness(self):
        passed = provider_conformance.Check(
            "api-live-probe", "pass", "synthetic API probe passed",
            {"provider": "openai", "model": "pinned-test-model"},
        )
        with patch.object(provider_conformance, "live_api_probe", return_value=passed):
            report = provider_conformance.run(
                live_api=True, api_providers=["openai"],
            )
        self.assertTrue(report["api_probe_ready"])
        self.assertFalse(report["release_ready"])
        self.assertEqual("pass", report["api_providers"][0]["status"])
        self.assertEqual("live_api", report["mode"])

    def test_direct_api_probe_requires_selected_provider_to_be_allowlisted(self):
        environment = {
            "AUREN_REMOTE_PROVIDER_ORDER": "groq",
            "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
            "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
            "OPENAI_API_KEY": "test-provider-secret",
            "OPENAI_MODEL": "pinned-test-model",
        }
        with patch(
            "portable.remote_model_providers.OpenAICompatibleProvider.from_environment",
            side_effect=AssertionError("unlisted provider must not be called"),
        ) as factory:
            result = provider_conformance.live_api_probe(
                "openai", environ=environment,
            )
        self.assertEqual("blocked", result.status)
        factory.assert_not_called()

    def test_run_deduplicates_selected_api_providers_to_avoid_double_charging(self):
        passed = provider_conformance.Check(
            "api-live-probe", "pass", "synthetic API probe passed",
            {"provider": "openai", "model": "pinned-test-model"},
        )
        with patch.object(provider_conformance, "live_api_probe", return_value=passed) as probe:
            report = provider_conformance.run(
                live_api=True, api_providers=["openai", "openai"],
            )
        self.assertEqual(1, probe.call_count)
        self.assertEqual(1, len(report["api_providers"]))

    def test_api_probe_cli_rejects_duplicate_provider_selection(self):
        completed = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--live-api",
                "--api-provider", "openai", "--api-provider", "openai", "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("duplicate --api-provider values", completed.stderr)

    def test_api_probe_cli_requires_explicit_provider_selection(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--live-api", "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("--live-api requires at least one --api-provider", completed.stderr)

    def test_static_harness_passes_without_installed_providers(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["release_ready"], "static checks must not claim live provider readiness")
        self.assertEqual(report["mode"], "static")
        self.assertEqual(report["static_contract"]["status"], "pass")
        self.assertTrue(all(provider["status"] == "pass" for provider in report["providers"]))

    def test_live_mode_is_explicit(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--live", source)
        self.assertIn("read-only", source)
        self.assertIn("AER_CONFORMANCE_OK", source)

    def test_static_mode_never_invokes_live_provider_probes(self):
        with patch.object(
            provider_conformance,
            "live_probe",
            side_effect=AssertionError("static mode must not invoke live provider probes"),
        ):
            report = provider_conformance.run(live=False)
        self.assertEqual("static", report["mode"])
        self.assertEqual("pass", report["static_contract"]["status"])
        self.assertFalse(report["release_ready"])

    def test_live_readiness_fails_closed_when_any_probe_is_not_pass(self):
        for unavailable_status in ("unsupported", "unavailable", "fail", "timeout"):
            with self.subTest(status=unavailable_status):
                def probe(provider):
                    status = unavailable_status if provider == "chatgpt" else "pass"
                    return provider_conformance.Check(
                        "live-probe", status, "test-only status fixture", {}
                    )

                with patch.object(provider_conformance, "live_probe", side_effect=probe):
                    report = provider_conformance.run(live=True)
                self.assertEqual("live", report["mode"])
                self.assertEqual("pass", report["static_contract"]["status"])
                self.assertFalse(
                    report["release_ready"],
                    f"non-passing probe status {unavailable_status!r} must not imply readiness",
                )


if __name__ == "__main__":
    unittest.main()
