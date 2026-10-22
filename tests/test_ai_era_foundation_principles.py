"""Regression tests for AUREN's 30-principle AI-era foundation contract."""
import hashlib
import json
import unittest
from pathlib import Path

from portable.ai_era_principles import PRINCIPLES, build_contract_receipt, canonical_registry, registry_digest
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = ["IPACT","VERA","PACT","INVAR","TRACE","R3","RAIL","CIG","BLAST","INDEP","OWN","LEARN","DELTA","HANDOFF","DEGRADE","TRUST","PROMOTE","ECONOMY","MEASURE","CONTEXT","DETERM","MINCORE","OUTCOME","MIGSAFE","PROVEN","HUMANE","ACCESS","REVERS","LIFECYCLE","PRIORITY"]


class TestAIEraFoundationPrinciples(unittest.TestCase):
    def test_registry_has_twenty_unique_principles_with_existing_owner_and_test(self):
        registry = canonical_registry()
        self.assertEqual(registry["schema_version"], "auren-ai-era-principles-v2")
        self.assertEqual([p["id"] for p in PRINCIPLES], EXPECTED_IDS)
        self.assertEqual(len({p["id"] for p in PRINCIPLES}), 30)
        for p in registry["principles"]:
            self.assertTrue(p["meaning"] and p["name"] and p["exception_policy"], p["id"])
            self.assertTrue((ROOT / p["owner"]).is_file(), p["id"])
            self.assertTrue((ROOT / p["test"]).is_file(), p["id"])

    def test_receipt_is_deterministic_and_binds_intent_paths_risk_revision_and_registry(self):
        receipt = build_contract_receipt("Implement principles", ["portable/orchestration.py"], "HIGH", "abc123")
        self.assertEqual(receipt, build_contract_receipt("Implement principles", ["portable/orchestration.py"] * 2, "high", "abc123"))
        self.assertEqual(receipt["schema_version"], "auren-ai-era-principle-receipt-v2")
        self.assertEqual(receipt["registry_digest"], registry_digest())
        self.assertEqual(receipt["principles"], EXPECTED_IDS)
        digest = receipt.pop("digest")
        expected = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, expected)
        self.assertNotEqual(build_contract_receipt("other intent", ["portable/orchestration.py"], "high", "abc123")["digest"], digest)
        self.assertNotEqual(build_contract_receipt("Implement principles", ["portable/other.py"], "high", "abc123")["digest"], digest)
        self.assertNotEqual(build_contract_receipt("Implement principles", ["portable/orchestration.py"], "critical", "abc123")["digest"], digest)
        self.assertNotEqual(build_contract_receipt("Implement principles", ["portable/orchestration.py"], "high", "def456")["digest"], digest)

    def test_receipt_rejects_missing_intent_and_unknown_risk(self):
        with self.assertRaisesRegex(ValueError, "non-empty intent"):
            build_contract_receipt(" ", [], "low")
        with self.assertRaisesRegex(ValueError, "risk must be one of"):
            build_contract_receipt("task", [], "unbounded")

    def test_orchestrator_records_principle_receipt_before_graph_nodes(self):
        observed = []
        graph = Graph([Node("inspect", NodeKind.DETERMINISTIC,
            lambda state: observed.append(state["ai_era_principles_contract"]["digest"]) or {"ok": True})])
        run = Orchestrator(graph).run("principle-contract-test", "Run a bounded documentation task",
            context={"changed_paths": ["docs/guide.md"], "risk": "low", "code_revision": "test-revision"})
        self.assertEqual(run.status, RunStatus.ACCEPTED)
        event = next(e for e in run.evidence if e.kind == "ai-era-principles-contract")
        payload = json.loads(event.summary)
        self.assertEqual(payload["code_revision"], "test-revision")
        self.assertEqual(payload["risk"], "low")
        self.assertEqual(len(payload["principles"]), 30)
        self.assertEqual(observed, [payload["digest"]])
        trace = next(e for e in run.trajectory if e.get("event") == "ai_era_principles_contract")
        self.assertEqual(trace["receipt_digest"], payload["digest"])
        self.assertEqual(trace["registry_digest"], payload["registry_digest"])

    def test_high_risk_guard_blocks_before_side_effects_and_keeps_principle_receipt(self):
        calls = []
        graph = Graph([Node("mutate", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True}, risk="high")])
        run = Orchestrator(graph).run("blocked-high-risk", "Change authentication for external tool execution",
            context={"changed_paths": ["portable/security/tool_runner.py"], "risk": "high"})
        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertTrue(any(e.kind == "ai-era-principles-contract" for e in run.evidence))
        self.assertTrue(any(e.kind == "engineering-design-review" for e in run.evidence))


if __name__ == "__main__":
    unittest.main()
