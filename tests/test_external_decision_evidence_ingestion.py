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
from portable.promotion_evidence_chain import PromotionEvidenceChain, PromotionEvidenceChainBuilder
from portable.causal_capability_promotion import CausalCapabilityPromotionGate
from tests.test_promotion_evidence_chain import (
    make_campaign, make_receipt, make_causal, make_attested_campaign, isolation_kwargs,
)
from portable.evidence_driven_decision_fabric import EvidenceDrivenDecisionFabric


def graph(tmp_path):
    return PersistentEvidenceGraph(
        PersistentMemory(Path(tmp_path) / "m.db", require_approval=False), "hws"
    )


def chain(campaign_digest="campaign-1", **overrides):
    # Structural-only fixture is intentionally used only for rejection tests.
    values = dict(
        campaign_digest=campaign_digest,
        run_receipt_digest="receipt-1",
        causal_evidence_digest="causal-1",
        capability_id="planner",
        intervention_id="intervention-1",
        eligible=True,
        reasons=(),
        attestation_evidence_digest="verified-attestation-fixture",
        causal_evidence_ids=(),
        isolation_evidence_digest="verified-isolation-fixture",
    )
    values.update(overrides)
    return PromotionEvidenceChain(**values)


def verified_chain(campaign, causal_evidence_ids=()):
    receipt = make_receipt(campaign)
    causal = make_causal(
        receipt, evidence_ids=(receipt.receipt_digest, *causal_evidence_ids)
    )
    decision = CausalCapabilityPromotionGate().evaluate(causal)
    return PromotionEvidenceChainBuilder(require_production_attestation=False).build(
        campaign, receipt, causal, decision,
        attested_campaign=make_attested_campaign(campaign, receipt),
        **isolation_kwargs(campaign, receipt),
    )


def outcome(digest="campaign-1"):
    return CampaignOutcome(digest, .9, .95, .1, .1, .8)


class ExternalDecisionEvidenceIngestionTests(unittest.TestCase):
    def test_ingests_only_independently_verified_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = graph(tmp)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            campaign = make_campaign()
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                campaign.campaign_digest,
            )
            verified = verified_chain(campaign, (e.evidence_id,))
            result = ingestor.ingest(verified, outcome(verified.campaign_digest), e)
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
                campaign = make_campaign()
                evidence = ExternalDecisionEvidence(
                    "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                    campaign.campaign_digest, **kwargs,
                )
                verified = verified_chain(campaign, (evidence.evidence_id,))
                with self.assertRaises(ValueError):
                    ingestor.ingest(
                        verified, outcome(verified.campaign_digest), evidence,
                    )

    def test_rejects_lineage_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = graph(tmp)
            ingestor = ExternalDecisionEvidenceIngestor(g)
            campaign = make_campaign()
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                "other-campaign",
            )
            verified = verified_chain(campaign, (e.evidence_id,))
            with self.assertRaises(ValueError):
                ingestor.ingest(verified, outcome(verified.campaign_digest), e)

    def test_rejects_observation_not_bound_to_causal_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            ingestor = ExternalDecisionEvidenceIngestor(graph(tmp))
            campaign = make_campaign()
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                campaign.campaign_digest,
            )
            verified = verified_chain(campaign, ("another-evidence-id",))
            with self.assertRaisesRegex(ValueError, "not bound"):
                ingestor.ingest(verified, outcome(verified.campaign_digest), e)

    def test_observation_digest_covers_trust_flags_and_rejects_tampering(self):
        with self.assertRaisesRegex(ValueError, "does not match canonical observation content"):
            ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                "campaign-1", evidence_id="forged-id",
            )
        with self.assertRaisesRegex(ValueError, "verified must be a boolean"):
            ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1,
                "campaign-1", verified="true",
            )

    def test_rejects_chain_without_attestation_evidence_digest(self):
        incomplete = chain(attestation_evidence_digest="")
        self.assertFalse(incomplete.complete)
        with tempfile.TemporaryDirectory() as tmp:
            ingestor = ExternalDecisionEvidenceIngestor(graph(tmp))
            e = ExternalDecisionEvidence(
                "verifier", "planner", "agent", "agent", .95, .92, 40, 1, "campaign-1"
            )
            incomplete = chain(attestation_evidence_digest="", causal_evidence_ids=(e.evidence_id,))
            with self.assertRaises(ValueError):
                ingestor.ingest(incomplete, outcome(), e)


if __name__ == "__main__":
    unittest.main()
