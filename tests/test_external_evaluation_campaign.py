import unittest

from portable.external_evaluation_campaign import (
    CampaignOutcome,
    CampaignRetestContract,
    ExternalEvaluationCampaign,
    LearningIntervention,
)


def campaign(**overrides):
    values = dict(
        campaign_id="external-1",
        campaign_version="1",
        arena_version="1",
        corpus_digest="c" * 64,
        generator_digest="g" * 64,
        oracle_digest="o" * 64,
        evaluator_version="eval-1",
        runtime_snapshot="runtime-1",
        case_ids=("a", "b", "c", "d"),
        holdout_case_ids=("c", "d"),
        novel_domain_case_ids=("c",),
        unfamiliar_tool_case_ids=("d",),
        external_attestation="attestation-1",
    )
    values.update(overrides)
    return ExternalEvaluationCampaign(**values)


class ExternalEvaluationCampaignTests(unittest.TestCase):
    def test_campaign_is_content_addressed_and_excludes_task_data(self):
        first = campaign()
        second = campaign()
        self.assertEqual(first.campaign_digest, second.campaign_digest)
        payload = first.as_dict()
        self.assertNotIn("task", payload)
        self.assertNotIn("answer", payload)
        self.assertFalse(first.trustworthy)
        self.assertTrue(first.has_attestation_material)

    def test_campaign_digest_excludes_attestation_signature(self):
        first = campaign(external_attestation="signature-one")
        second = campaign(external_attestation="signature-two")
        self.assertEqual(first.campaign_digest, second.campaign_digest)
        self.assertNotEqual(first.as_dict()["external_attestation"], second.as_dict()["external_attestation"])

    def test_campaign_rejects_unknown_dimension_case(self):
        with self.assertRaisesRegex(ValueError, "unknown case id"):
            campaign(novel_domain_case_ids=("missing",))

    def test_raw_signature_or_contamination_does_not_establish_trust(self):
        self.assertFalse(campaign().trustworthy)
        self.assertFalse(campaign(contamination_detected=True).structurally_valid)
        self.assertFalse(campaign(external_attestation="").has_attestation_material)

    def test_outcome_requires_bounded_metrics(self):
        outcome = CampaignOutcome(
            campaign_digest=campaign().campaign_digest,
            holdout_pass_rate=0.8,
            holdout_verification_rate=0.8,
            generalization_gap=0.2,
            calibration_error=0.1,
            efficiency_score=0.7,
        )
        self.assertTrue(outcome.evidence_eligible)
        with self.assertRaisesRegex(ValueError, "finite number"):
            CampaignOutcome(campaign().campaign_digest, float("nan"), 0.8, 0.2, 0.1, 0.7)

    def test_retest_requires_fresh_independent_holdout(self):
        intervention = LearningIntervention(
            "i-1", "planner", "new planning policy helps", "improve holdout transfer",
            rollback_condition="regression on control group",
        )
        contract = CampaignRetestContract(campaign().campaign_digest, intervention.intervention_digest)
        self.assertTrue(contract.new_holdout_required)
        with self.assertRaises(ValueError):
            CampaignRetestContract(
                campaign().campaign_digest, intervention.intervention_digest,
                new_holdout_required=False,
            )

    def test_campaign_rejects_truthy_contamination_string(self):
        with self.assertRaisesRegex(ValueError, "contamination_detected must be a boolean"):
            campaign(contamination_detected="false")


if __name__ == "__main__":
    unittest.main()
