import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ai-harness"))

from runtime.capability_catalog import select_capabilities, validate_plan


class CapabilityCatalogTests(unittest.TestCase):
    def test_implementation_gets_builder_and_verifier(self):
        plan = select_capabilities({"mode": "implement", "risk": "low", "uncertainty": "known"})
        self.assertEqual(plan["selected"], ["planner", "builder", "verifier"])
        self.assertEqual(validate_plan(plan)["passed"], True)

    def test_high_risk_gets_independent_reviewers(self):
        plan = select_capabilities({"mode": "implement", "risk": "high", "uncertainty": "known"})
        self.assertIn("security_reviewer", plan["selected"])
        self.assertIn("reviewer", plan["selected"])
        self.assertIn("builder", plan["mutating_capabilities"])

    def test_rca_does_not_select_builder(self):
        plan = select_capabilities({"mode": "rca", "risk": "medium", "uncertainty": "unknown"})
        self.assertIn("explorer", plan["selected"])
        self.assertIn("rca_investigator", plan["selected"])
        self.assertNotIn("builder", plan["selected"])

    def test_discovery_does_not_make_extension_an_execution_hint(self):
        plan = select_capabilities(
            {"mode": "research"},
            extensions={
                "mcp": {
                    "available": True,
                    "trust_class": "external-unverified",
                    "requires_approval": True,
                    "permissions": ["read-repository"],
                    "output_limit_chars": 1000,
                },
                "trusted_readonly": {
                    "available": True,
                    "trust_class": "trusted",
                    "trust_granted": True,
                    "artifact_fresh": True,
                    "requires_approval": False,
                    "permissions": ["read-repository"],
                    "output_limit_chars": 1000,
                },
            },
        )
        self.assertEqual(plan["extension_hints"], ["trusted_readonly"])
        self.assertIn("explicit_trust_grant_required", plan["extension_decisions"]["mcp"]["reasons"])
        self.assertTrue(validate_plan(plan)["passed"])

    def test_invalid_plan_is_rejected(self):
        result = validate_plan({"selected": ["builder", "builder"], "max_parallel_read_only": 1})
        self.assertFalse(result["passed"])
        self.assertIn("duplicate_capability", result["reasons"])

    def test_unapproved_extension_hint_is_rejected(self):
        result = validate_plan({
            "selected": ["planner"],
            "max_parallel_read_only": 1,
            "extension_hints": ["mcp"],
            "extension_decisions": {"mcp": {"allowed": False, "reasons": ["approval_required"]}},
        })
        self.assertFalse(result["passed"])
        self.assertIn("unapproved_extension_hint", result["reasons"])


if __name__ == "__main__":
    unittest.main()
