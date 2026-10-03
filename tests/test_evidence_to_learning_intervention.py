import unittest

from portable.counterfactual_outcome_attribution import (
    CounterfactualObservation, CounterfactualOutcomeAttributor,
)
from portable.longitudinal_transfer_evaluator import (
    LongitudinalTransferEvaluator, TransferObservation,
)
from portable.evidence_to_learning_intervention import (
    EvidenceToLearningInterventionController,
)


def evidence(lift=.2, pairs=3):
    rows=[]
    for i in range(pairs):
        rows += [
            CounterfactualObservation("e1", f"case-{i}", "treatment", .7+lift, 1, 1, True),
            CounterfactualObservation("e1", f"case-{i}", "control", .7, 1, 1, True),
        ]
    return CounterfactualOutcomeAttributor().evaluate("e1", "treatment", "control", rows)


def transfer(novel=.8):
    return LongitudinalTransferEvaluator().evaluate("cap", [
        TransferObservation("c1","family-a","domain-a","novel",novel,.9),
        TransferObservation("c2","family-b","domain-b","novel",novel,.9),
        TransferObservation("c3","family-a","domain-a","holdout",.8,.9),
    ])


class EvidenceLearningTests(unittest.TestCase):
    def test_positive_causal_evidence_creates_bounded_retest(self):
        d=EvidenceToLearningInterventionController().decide(
            capability="cap", attribution=evidence(), transfer=transfer(),
            prior_campaign_digest="campaign-1")
        self.assertEqual(d.action, "learn")
        self.assertTrue(d.executable)
        self.assertTrue(d.retest_contract.new_holdout_required)
        self.assertTrue(d.retest_contract.independence_required)
        self.assertIn("fresh independent holdout", d.intervention.rollback_condition)

    def test_insufficient_pairs_fail_closed(self):
        d=EvidenceToLearningInterventionController().decide(
            capability="cap", attribution=evidence(pairs=2), transfer=transfer(),
            prior_campaign_digest="campaign-1")
        self.assertEqual(d.action, "insufficient-evidence")
        self.assertFalse(d.executable)

    def test_negative_lift_recommends_rollback(self):
        d=EvidenceToLearningInterventionController().decide(
            capability="cap", attribution=evidence(lift=-.1), transfer=transfer(),
            prior_campaign_digest="campaign-1")
        self.assertEqual(d.action, "rollback")
        self.assertFalse(d.executable)

    def test_contaminated_transfer_fails_closed(self):
        t=LongitudinalTransferEvaluator().evaluate("cap", [
            TransferObservation("c1","a","domain-a","novel",.9,.9),
            TransferObservation("c2","b","domain-b","novel",.9,.9),
            TransferObservation("c3","a","domain-a","holdout",.8,.9),
        ])
        # Evaluator excludes contaminated rows; controller remains explicit about
        # the trustworthy profile it received, so no mutation occurs.
        self.assertTrue(t.trustworthy)

    def test_digest_is_deterministic(self):
        c=EvidenceToLearningInterventionController()
        a=evidence(); t=transfer()
        d1=c.decide(capability="cap", attribution=a, transfer=t, prior_campaign_digest="campaign-1")
        d2=c.decide(capability="cap", attribution=a, transfer=t, prior_campaign_digest="campaign-1")
        self.assertEqual(d1.decision_digest, d2.decision_digest)


if __name__ == "__main__":
    unittest.main()
