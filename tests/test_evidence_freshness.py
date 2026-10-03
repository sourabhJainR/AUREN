from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from portable.evidence_driven_decision_fabric import EvidenceDrivenDecisionFabric
from portable.evidence_freshness_policy import EvidenceFreshnessPolicy
from portable.external_decision_evidence_ingestion import ExternalDecisionEvidence, ExternalDecisionEvidenceIngestor
from portable.external_evaluation_campaign import CampaignOutcome
from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.persistent_memory import PersistentMemory
from portable.promotion_evidence_chain import PromotionEvidenceChain


class EvidenceFreshnessTests(unittest.TestCase):
    def test_old_timestamp_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = PersistentEvidenceGraph(PersistentMemory(Path(tmp) / "m.db", require_approval=False), "hws")
            cap = g.add_node("capability", "planner")
            old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            obs = g.add_node("observation", "old", {
                "kind": "decision-observation", "task_family": "verifier",
                "capability": "planner", "provider": "agent", "tool_path": "agent",
                "success": "1", "quality": "1", "duration": "1", "cost": "1",
                "verified": "true", "contaminated": "false", "observed_at": old,
            })
            g.add_edge(obs, "supports", cap)
            plan = EvidenceDrivenDecisionFabric(g).plan(
                "verifier", "planner",
                ({"provider": "agent", "tool_path": "agent"},),
                freshness_policy=EvidenceFreshnessPolicy(max_age_seconds=86400),
                now=datetime.now(timezone.utc),
            )
            self.assertTrue(plan.fallback)
            self.assertEqual(plan.evidence_ids, ())

    def test_fresh_external_ingestion_can_steer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            g = PersistentEvidenceGraph(PersistentMemory(db, require_approval=False), "hws")
            chain = PromotionEvidenceChain("c", "r", "x", "planner", "i", True, ())
            outcome = CampaignOutcome("c", .9, .95, .1, .1, .8)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            for i in range(8):
                ingestor.ingest(
                    chain, outcome,
                    ExternalDecisionEvidence("verifier", "planner", "agent", "agent",
                                             .95, .95, 30, 1, "c", evidence_id=f"fresh-{i}"),
                )
            plan = EvidenceDrivenDecisionFabric(g).plan(
                "verifier", "planner",
                ({"provider": "agent", "tool_path": "agent"},),
                min_confidence=.3,
            )
            self.assertFalse(plan.fallback)
            self.assertEqual(plan.selected.provider, "agent")


if __name__ == "__main__":
    unittest.main()
