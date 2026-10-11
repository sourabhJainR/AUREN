import unittest

from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest
from portable.attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation
from portable.promotion_evidence_chain import PromotionEvidenceChainBuilder


def make_campaign(**overrides):
    oracle = oracle_registry_digest(("oracle-1",))
    values = dict(
        campaign_id="c1", campaign_version="1", arena_version="1", corpus_digest="c" * 64,
        generator_digest="g" * 64, oracle_digest=oracle, evaluator_version="eval",
        runtime_snapshot="runtime", case_ids=("a", "b"), holdout_case_ids=("b",),
        external_attestation="attested",
    )
    values.update(overrides)
    return ExternalEvaluationCampaign(**values)


def make_attested_campaign(campaign, *, signature=None):
    signature = campaign.external_attestation if signature is None else signature
    attestation = EvaluationAttestation(
        campaign.campaign_digest, campaign.corpus_digest, campaign.oracle_digest,
        campaign.evaluator_version, "external-signer", signature,
    )
    verifier = SeparationAwareAttestor(
        lambda value: value.signature == campaign.external_attestation,
        AttestationTrustPolicy(frozenset({"external-signer"})),
        lambda principal, role: (
            (role == "evaluator" and principal == "runtime-evaluator")
            or (role == "oracle" and principal == "independent-oracle")
        ),
    )
    return verifier.verify(
        attestation,
        evaluator_id="runtime-evaluator",
        oracle_id="independent-oracle",
        expected_campaign_digest=campaign.campaign_digest,
        expected_corpus_digest=campaign.corpus_digest,
        expected_oracle_digest=campaign.oracle_digest,
    )


def make_receipt(campaign, **overrides):
    values = dict(
        run_id="run", arena_version="1", corpus_digest=campaign.corpus_digest,
        manifest_digest="m" * 64, oracle_digest=campaign.oracle_digest,
        runtime_snapshot=campaign.runtime_snapshot, evaluator_version=campaign.evaluator_version,
        case_ids=campaign.case_ids, holdout_case_ids=campaign.holdout_case_ids,
        passed_case_ids=("a", "b"), verified_case_ids=("a", "b"), duration_ms=100,
        campaign_digest=campaign.campaign_digest,
    )
    values.update(overrides)
    return ArenaRunReceipt(**values)


def make_causal(receipt, **overrides):
    values = dict(
        capability_id="planner", intervention_id="i1", baseline_score=0.70, control_score=0.70,
        treatment_score=0.80, holdout_score=0.74, attribution_confidence=0.9,
        fresh_holdout=True, independent_oracle=True, contamination_detected=False,
        evidence_ids=(receipt.receipt_digest, "e2"),
    )
    values.update(overrides)
    return CausalPromotionEvidence(**values)


class PromotionEvidenceChainTests(unittest.TestCase):
    def test_chain_requires_verified_attestation_and_consistent_lineage(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertTrue(chain.complete, chain.reasons)
        self.assertEqual(chain.campaign_digest, campaign.campaign_digest)
        self.assertTrue(chain.attestation_evidence_digest)

    def test_chain_blocks_mismatched_or_untrusted_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, corpus_digest="different", contamination_detected=True)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("corpus" in reason for reason in chain.reasons))

    def test_chain_rejects_receipt_without_campaign_binding(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, campaign_digest="")
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("campaign digests differ" in reason for reason in chain.reasons))

    def test_chain_requires_receipt_digest_in_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt, evidence_ids=("unrelated-evidence", "e2"))
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this run receipt" in reason for reason in chain.reasons))

    def test_decision_must_reference_exact_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal_a = make_causal(receipt)
        decision_a = CausalCapabilityPromotionGate().evaluate(causal_a)
        causal_b = make_causal(receipt, treatment_score=0.85)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal_b, decision_a, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this causal evidence" in reason for reason in chain.reasons))

    def test_chain_rejects_bad_attestation_even_with_nonempty_signature(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        attestation = make_attested_campaign(campaign, signature="forged")
        self.assertFalse(attestation.trustworthy)
        chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision, attested_campaign=attestation
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("not independently verified" in reason for reason in chain.reasons))


if __name__ == "__main__":
    unittest.main()
