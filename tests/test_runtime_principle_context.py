from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from runtime import _principle_context_inputs


class RuntimePrincipleContextTests(unittest.TestCase):
    def test_runtime_binds_risk_observed_paths_and_explicit_revision(self):
        inputs = _principle_context_inputs(
            {"risk": "HIGH"},
            {
                "scope_paths": ["portable/allowed_but_unchanged.py"],
                "changed_paths": ["manifest-claimed.py"],
                "design_contract": {"security": "reuse existing authorization boundary"},
                "code_revision": "candidate-revision-123",
            },
            changed_paths=["portable/changed.py", "portable/changed.py", "", None],
        )
        self.assertEqual(inputs["risk"], "high")
        self.assertEqual(inputs["changed_paths"], ["portable/changed.py"])
        self.assertNotIn("portable/allowed_but_unchanged.py", inputs["changed_paths"])
        self.assertNotIn("manifest-claimed.py", inputs["changed_paths"])
        self.assertEqual(inputs["design_contract"], {"security": "reuse existing authorization boundary"})
        self.assertEqual(inputs["code_revision"], "candidate-revision-123")

    def test_runtime_does_not_invent_revision_or_design_contract(self):
        inputs = _principle_context_inputs(
            {"risk": "unrecognized"},
            {"design_contract": ["not", "a", "mapping"], "code_revision": 7},
        )
        self.assertEqual(inputs["risk"], "low")
        self.assertEqual(inputs["changed_paths"], [])
        self.assertEqual(inputs["design_contract"], {})
        self.assertEqual(inputs["code_revision"], "")


if __name__ == "__main__":
    unittest.main()
