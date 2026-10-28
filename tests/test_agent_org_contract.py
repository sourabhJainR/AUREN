from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "agent_org.v1.json"
REQUIRED_ROLE_FIELDS = {
    "id", "title", "expertise", "reports_to", "accountable_for",
    "allowed_action_classes", "prohibited_actions", "approval_boundary",
}


class AgentOrganizationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.roles = cls.registry["roles"]
        cls.by_id = {role["id"]: role for role in cls.roles}

    def test_registry_is_descriptive_not_an_authority_grant(self):
        self.assertEqual("auren-ai-engineering-org-v1", self.registry["schema_version"])
        self.assertIn("not itself an authorization mechanism", self.registry["purpose"])
        self.assertEqual("not_certified_by_this_registry",
                         self.registry["status"]["runtime_enforcement"])
        self.assertEqual("not_verified", self.registry["status"]["production_deployment"])
        self.assertTrue(any("canonical supervisor" in rule.lower()
                            for rule in self.registry["principles"]))
        self.assertTrue(any("cannot approve its own" in rule.lower()
                            for rule in self.registry["principles"]))

    def test_roles_are_unique_complete_and_have_valid_reporting_parents(self):
        role_ids = [role["id"] for role in self.roles]
        self.assertEqual(len(role_ids), len(set(role_ids)))
        self.assertGreaterEqual(len(role_ids), 9)
        external_ids = {item["id"] for item in self.registry["external_entities"]}
        known_parents = set(role_ids) | external_ids
        for role in self.roles:
            with self.subTest(role=role["id"]):
                self.assertTrue(REQUIRED_ROLE_FIELDS <= set(role))
                self.assertTrue(role["title"].strip())
                self.assertTrue(role["expertise"])
                self.assertTrue(role["accountable_for"])
                self.assertTrue(role["allowed_action_classes"])
                self.assertTrue(role["prohibited_actions"])
                self.assertTrue(role["approval_boundary"].strip())
                self.assertIn(role["reports_to"], known_parents)
                self.assertNotEqual(role["id"], role["reports_to"])

    def test_reporting_graph_has_no_cycles(self):
        for role in self.roles:
            seen = set()
            current = role["id"]
            while current in self.by_id:
                self.assertNotIn(current, seen, f"reporting cycle from {role['id']}")
                seen.add(current)
                current = self.by_id[current]["reports_to"]

    def test_independent_evaluator_is_outside_implementation_reporting_line(self):
        evaluator = self.by_id["EVAL-01"]
        self.assertEqual("independent_governance_council", evaluator["reports_to"])
        self.assertIn("separately controlled from the candidate implementation", evaluator["approval_boundary"])
        self.assertIn("modify_candidate_to_pass_its_own_evaluation",
                      evaluator["prohibited_actions"])
        self.assertIn("independent_governance_council",
                      {item["id"] for item in self.registry["external_entities"]})

    def test_executive_and_manager_roles_cannot_override_mandatory_gates(self):
        ceo = self.by_id["CEO-01"]
        cto = self.by_id["CTO-01"]
        manager = self.by_id["EM-ENG-01"]
        self.assertIn("bypass_security_or_release_gates", ceo["prohibited_actions"])
        self.assertIn("waive_required_checks", cto["prohibited_actions"])
        self.assertIn("mark_unverified_work_complete", manager["prohibited_actions"])

    def test_specialist_roles_define_scope_and_review_boundaries(self):
        for role_id in ("BE-01", "ARCH-01", "QA-01", "SRE-01", "SEC-01", "FIN-01"):
            role = self.by_id[role_id]
            with self.subTest(role=role_id):
                self.assertTrue(role["expertise"])
                self.assertTrue(role["accountable_for"])
                self.assertTrue(role["approval_boundary"])


if __name__ == "__main__":
    unittest.main()
