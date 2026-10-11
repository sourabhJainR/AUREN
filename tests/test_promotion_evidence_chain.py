import unittest

from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest
from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.promotion_evidence_chain import PromotionEvidenceChainBuilder


def make_campaign():
    oracle = oracle_registry_digest(("oracle-1",))
    return ExternalEvaluationCampaign(
        "c1", "1", "1", "c" * 64, "g" * 64, oracle, "eval", "runtime",
        ("a", "b"), ("b",), external_attestation="attested",
    )


def make_receipt(campaign, **overrides):
    values = dict(
        run_id="run",
        arena_version="1",
        corpus_digest=campaign.corpus_digest,
        manifest_digest="m" * 64,
        oracle_digest=campaign.oracle_digest,
        runtime_snapshot=campaign.runtime_snapshot,
        evaluator_version=campaign.evaluator_version,
        case_ids=campaign.case_ids,
        holdout_case_ids=campaign.holdout_case_ids,
        passed_case_ids=("a", "b"),
        verified_case_ids=("a", "b"),
        duration_ms=100,
        campaign_digest=campaign.campaign_digest,
    )
    values.update(overrides)
    return ArenaRunReceipt(**values)


def make_causal(receipt, **overrides):
    values = dict(
        capability_id="planner",
        intervention_id="i1",
        baseline_score=0.70,
        control_score=0.70,
        treatment_score=0.80,
        holdout_score=0.74,
        attribution_confidence=0.9,
        fresh_holdout=True,
        independent_oracle=True,
        contamination_detected=False,
        evidence_ids=(receipt.receipt_digest, "e2"),
    )
    values.update(overrides)
    return CausalPromotionEvidence(**values)


class PromotionEvidenceChainTests(unittest.TestCase):
    def test_chain_requires_consistent_external_lineage(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(campaign, receipt, causal, decision)
        self.assertTrue(chain.complete, chain.reasons)
        self.assertEqual(chain.campaign_digest, campaign.campaign_digest)

    def test_chain_blocks_mismatched_or_untrusted_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, corpus_digest="different", contamination_detected=True)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(campaign, receipt, causal, decision)
        self.assertFalse(chain.complete)
        self.assertTrue(any("corpus" in reason for reason in chain.reasons))

    def test_chain_rejects_receipt_without_campaign_binding(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, campaign_digest="")
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(campaign, receipt, causal, decision)
        self.assertFalse(chain.complete)
        self.assertTrue(any("campaign digests differ" in reason for reason in chain.reasons))

    def test_chain_requires_receipt_digest_in_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt, evidence_ids=("unrelated-evidence", "e2"))
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(campaign, receipt, causal, decision)
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this run receipt" in reason for reason in chain.reasons))

    def test_decision_must_reference_exact_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal_a = make_causal(receipt)
        decision_a = CausalCapabilityPromotionGate().evaluate(causal_a)
        causal_b = make_causal(receipt, treatment_score=0.85)
        chain = PromotionEvidenceChainBuilder().build(campaign, receipt, causal_b, decision_a)
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this causal evidence" in reason for reason in chain.reasons))


if __name__ == "__main__":
    unittest.main()
