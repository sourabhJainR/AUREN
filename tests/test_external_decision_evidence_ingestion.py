from pathlib import Path
import tempfile
import unittest

from portable.external_decision_evidence_ingestion import (
    ExternalDecisionEvidence,
    ExternalDecisionEvidenceIngestor,
)
from portable.external_evaluation_campaign import CampaignOutcome
from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.persistent_memory import PersistentMemory
from portable.promotion_evidence_chain import PromotionEvidenceChain
from portable.evidence_driven_decision_fabric import EvidenceDrivenDecisionFabric


def graph(tmp_path):
    return PersistentEvidenceGraph(
        PersistentMemory(Path(tmp_path) / "m.db", require_approval=False), "hws"
    )


def chain(campaign_digest="campaign-1", **overrides):
    values = dict(
        campaign_digest=campaign_digest,
        run_receipt_digest="receipt-1",
        causal_evidence_digest="causal-1",
        capability_id="planner",
        intervention_id="intervention-1",
        eligible=True,
        reasons=(),
        attestation_evidence_digest="verified-attestation-fixture",
    )
    values.update(overrides)
    return PromotionEvidenceChain(**values)


def outcome(digest="campaign-1"):
    return CampaignOutcome(digest, .9, .95, .1, .1, .8)


class ExternalDecisionEvidenceIngestionTests(unittest.TestCase):
    def test_ingests_only_independently_verified_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = graph(tmp)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                "campaign-1",
            )
            result = ingestor.ingest(chain(), outcome(), e)
            self.assertTrue(result.ingested)
            self.assertEqual(len(g.nodes(kind="observation")), 1)
            plan = EvidenceDrivenDecisionFabric(g).plan(
                "verifier", "planner",
                (
                    {"provider": "agent", "tool_path": "agent", "expected": {"duration": 40, "cost": 1, "quality": .5, "failure": .5}},
                    {"provider": "local", "tool_path": "local", "expected": {"duration": 40, "cost": .5, "quality": .5, "failure": .5}},
                ),
                min_confidence=.05,
            )
            self.assertEqual(plan.selected.provider, "agent")
            self.assertFalse(plan.fallback)

    def test_rejects_contaminated_or_unverified_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = graph(tmp)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            for kwargs in (
                {"contaminated": True},
                {"verified": False},
                {"oracle_independent": False},
            ):
                with self.assertRaises(ValueError):
                    ingestor.ingest(
                        chain(), outcome(),
                        ExternalDecisionEvidence(
                            "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                            "campaign-1", **kwargs,
                        ),
                    )

    def test_rejects_lineage_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = graph(tmp)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                "other-campaign",
            )
            with self.assertRaises(ValueError):
                ingestor.ingest(chain(), outcome(), e)

    def test_rejects_chain_without_attestation_evidence_digest(self):
        incomplete = chain(attestation_evidence_digest="")
        self.assertFalse(incomplete.complete)
        with tempfile.TemporaryDirectory() as tmp:
            ingestor = ExternalDecisionEvidenceIngestor(graph(tmp))
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1, "campaign-1"
            )
            with self.assertRaises(ValueError):
                ingestor.ingest(incomplete, outcome(), e)


if __name__ == "__main__":
    unittest.main()
