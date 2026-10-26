import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ai-harness"))

from runtime.capability_catalog import rank_extension_candidates, select_capabilities, validate_plan


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
            {
                "mode": "research",
                "required_extension_capabilities": ["research"],
                "privacy_requirement": "standard",
                "allow_external_extensions": True,
            },
            extensions={
                "mcp": {
                    "available": True,
                    "manifest_valid": True,
                    "trust_class": "external-unverified",
                    "privacy_class": "external",
                    "cost_class": "low",
                    "artifact_fresh": True,
                    "requires_approval": True,
                    "permissions": ["read-repository"],
                    "side_effects": [],
                    "capabilities": ["research"],
                    "output_limit_chars": 1000,
                },
                "trusted_readonly": {
                    "available": True,
                    "manifest_valid": True,
                    "trust_class": "trusted",
                    "privacy_class": "local-only",
                    "cost_class": "low",
                    "artifact_fresh": True,
                    "requires_approval": False,
                    "permissions": [],
                    "side_effects": [],
                    "capabilities": ["research"],
                    "output_limit_chars": 1000,
                },
            },
            extension_policy={
                "privacy_requirement": "local-only",
                "allow_external": False,
                "grants": {
                    "trusted_readonly": {
                        "trust_granted": True,
                        "artifact_fresh": True,
                        "requested_permissions": [],
                    }
                },
            },
        )
        self.assertEqual(plan["extension_hints"], ["trusted_readonly"])
        self.assertIn("explicit_trust_grant_required", plan["extension_decisions"]["mcp"]["reasons"])
        self.assertIn("external_data_use_not_authorized", plan["extension_decisions"]["mcp"]["reasons"])
        self.assertTrue(validate_plan(plan)["passed"])

    def test_extension_ranking_respects_cost_capability_privacy_and_trust(self):
        base = {
            "available": True,
            "manifest_valid": True,
            "trust_class": "trusted",
            "privacy_class": "local-only",
            "cost_class": "low",
            "artifact_fresh": True,
            "requires_approval": True,
            "permissions": ["read-repository"],
            "side_effects": [],
            "capabilities": ["ast", "graph", "impact"],
            "output_limit_chars": 1000,
        }
        extensions = {
            "cheap": dict(base),
            "medium": {**base, "cost_class": "medium"},
            "external": {**base, "privacy_class": "external"},
            "untrusted": {**base, "trust_class": "external-unverified"},
        }
        grants = {
            name: {
                "trust_granted": True,
                "approval_granted": True,
                "artifact_fresh": True,
                "requested_permissions": ["read-repository"],
            }
            for name in extensions
        }
        result = rank_extension_candidates(
            {"required_capabilities": ["ast", "graph"]},
            extensions,
            grants=grants,
            privacy_requirement="local-only",
            max_cost_class="medium",
        )
        self.assertEqual("cheap", result["selected"])
        self.assertEqual(["cheap", "medium"], result["eligible"])
        self.assertIn("local_only_privacy_required", result["excluded"]["external"])
        self.assertIn("explicit_trust_grant_required", result["excluded"]["untrusted"])

    def test_extension_ranking_falls_back_when_no_candidate_is_safe(self):
        result = rank_extension_candidates(
            {"required_capabilities": ["video"]},
            {
                "external_tool": {
                    "available": True,
                    "manifest_valid": True,
                    "trust_class": "external-unverified",
                    "privacy_class": "external",
                    "cost_class": "low",
                    "artifact_fresh": True,
                    "requires_approval": True,
                    "permissions": ["read-repository"],
                    "side_effects": [],
                    "capabilities": ["video"],
                    "output_limit_chars": 1000,
                }
            },
            grants={"external_tool": {"trust_granted": True, "approval_granted": True, "artifact_fresh": True}},
            privacy_requirement="local-only",
        )
        self.assertIsNone(result["selected"])
        self.assertEqual("repository_native", result["fallback"])
        self.assertIn("external_data_use_not_authorized", result["excluded"]["external_tool"])

    def test_unknown_privacy_falls_back_instead_of_guessing(self):
        metadata = {
            "available": True,
            "manifest_valid": True,
            "trust_class": "trusted",
            "privacy_class": "unknown",
            "cost_class": "low",
            "artifact_fresh": True,
            "requires_approval": False,
            "permissions": ["read-repository"],
            "side_effects": [],
            "capabilities": ["ast"],
            "output_limit_chars": 1000,
        }
        result = rank_extension_candidates(
            {"required_capabilities": ["ast"]},
            {"unknown_privacy": metadata},
            grants={"unknown_privacy": {
                "trust_granted": True,
                "artifact_fresh": True,
                "requested_permissions": ["read-repository"],
            }},
        )
        self.assertIsNone(result["selected"])
        self.assertIn("privacy_class_unverified", result["excluded"]["unknown_privacy"])
        self.assertEqual("repository_native", result["fallback"])

    def test_extension_router_combines_complementary_safe_extensions(self):
        base = {
            "available": True,
            "manifest_valid": True,
            "trust_class": "trusted",
            "privacy_class": "local-only",
            "cost_class": "low",
            "artifact_fresh": True,
            "requires_approval": False,
            "permissions": [],
            "side_effects": [],
            "output_limit_chars": 1000,
        }
        extensions = {
            "ast_tool": {**base, "capabilities": ["ast"]},
            "video_tool": {**base, "capabilities": ["video"]},
        }
        grants = {
            name: {"trust_granted": True, "artifact_fresh": True, "requested_permissions": []}
            for name in extensions
        }
        result = rank_extension_candidates(
            {"required_capabilities": ["ast", "video"]},
            extensions,
            grants=grants,
            privacy_requirement="local-only",
        )
        self.assertEqual(["ast_tool", "video_tool"], result["selected_extensions"])
        self.assertIsNone(result["fallback"])

        incomplete = rank_extension_candidates(
            {"required_capabilities": ["ast", "video"]},
            {"ast_tool": extensions["ast_tool"]},
            grants={"ast_tool": grants["ast_tool"]},
            privacy_requirement="local-only",
        )
        self.assertEqual([], incomplete["selected_extensions"])
        self.assertEqual("repository_native", incomplete["fallback"])
        self.assertEqual(["video"], incomplete["uncovered_capabilities"])

    def test_extension_ranking_rejects_invalid_policy_inputs(self):
        with self.assertRaisesRegex(ValueError, "max_cost_class"):
            rank_extension_candidates({}, {}, max_cost_class="unknown")

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
