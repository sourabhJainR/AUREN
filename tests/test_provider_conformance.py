from __future__ import annotations

import json
import subprocess
import sys
import unittest
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
        self.assertTrue(report["release_ready"])
        self.assertEqual(report["mode"], "static")

    def test_live_mode_is_explicit(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--live", source)
        self.assertIn("read-only", source)
        self.assertIn("AUREN_CONFORMANCE_OK", source)


if __name__ == "__main__":
    unittest.main()
