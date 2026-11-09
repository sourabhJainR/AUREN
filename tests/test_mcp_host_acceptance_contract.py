from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / ".ai-harness" / "PROVIDER_MATRIX.json"

CODING_HOSTS = (
    "codex_cli", "codex_app", "claude_code_cli", "claude_vscode",
    "gemini_cli", "gemini_code_assist_vscode", "cursor",
    "github_copilot", "visual_studio_ide",
)

ORCHESTRATION_FRAMEWORKS = (
    "openai_agents_sdk", "autogen", "pydantic_ai", "langchain_langgraph",
    "n8n", "crewai", "semantic_kernel", "haystack", "flowise",
    "openhands_local",
)


class MCPHostAcceptanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        cls.hosts = cls.matrix["platform_integrations"]

    def test_required_coding_hosts_are_explicitly_classified(self):
        for name in CODING_HOSTS:
            with self.subTest(host=name):
                host = self.hosts[name]
                self.assertIn(host["support"], {"repository_configured", "partial"})
                self.assertIs(host["live_verified"], False)
                self.assertTrue(host.get("current_gap"))
                self.assertTrue(host.get("next_steps"))

    def test_orchestration_frameworks_are_not_mislabeled_as_live_verified(self):
        for name in ORCHESTRATION_FRAMEWORKS:
            with self.subTest(framework=name):
                host = self.hosts[name]
                self.assertEqual("orchestration_framework", host["kind"])
                self.assertIn(host["support"], {"partial", "not_native", "not_wired"})
                self.assertIs(host["live_verified"], False)
                self.assertTrue(host.get("current_gap"))

    def test_no_untested_host_can_claim_live_acceptance(self):
        for name, host in self.hosts.items():
            if host.get("live_verified") is True:
                with self.subTest(host=name):
                    self.assertTrue(host.get("acceptance_evidence_ref"))
                    self.assertTrue(host.get("accepted_client_version"))
                    self.assertTrue(host.get("accepted_at"))


if __name__ == "__main__":
    unittest.main()
