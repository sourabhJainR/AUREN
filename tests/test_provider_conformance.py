from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import provider_conformance
from portable.remote_model_providers import PROVIDER_SPECS
from pathlib import Path

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
