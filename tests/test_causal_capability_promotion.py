import unittest

from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence


def evidence(**overrides):
    values = dict(
        capability_id="planner",
        intervention_id="i1",
        baseline_score=0.70,
        control_score=0.71,
        treatment_score=0.80,
        holdout_score=0.74,
        attribution_confidence=0.90,
        fresh_holdout=True,
        independent_oracle=True,
        contamination_detected=False,
        evidence_ids=("e1", "e2", "e3"),
    )
    values.update(overrides)
    return CausalPromotionEvidence(**values)


class CausalPromotionTests(unittest.TestCase):
    def test_gate_requires_control_lift_and_fresh_holdout_lift(self):
        decision = CausalCapabilityPromotionGate().evaluate(evidence())
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.state, "eligible")
        self.assertAlmostEqual(evidence().treatment_lift, 0.09)

    def test_regression_or_contamination_blocks(self):
        gate = CausalCapabilityPromotionGate()
        self.assertFalse(gate.evaluate(evidence(contamination_detected=True)).eligible)
        self.assertFalse(gate.evaluate(evidence(control_score=0.80)).eligible)

    def test_missing_fresh_holdout_blocks(self):
        self.assertFalse(CausalCapabilityPromotionGate().evaluate(evidence(fresh_holdout=False)).eligible)

    def test_duplicate_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique independent evidence ids"):
            evidence(evidence_ids=("e1", "e1"))

    def test_truthy_strings_cannot_bypass_trust_flags(self):
        for name in ("fresh_holdout", "independent_oracle", "contamination_detected"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"{name} must be a boolean"):
                evidence(**{name: "false"})

    def test_scores_must_be_finite_numbers_not_booleans(self):
        for value in (True, "0.9", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evidence(attribution_confidence=value)


if __name__ == "__main__":
    unittest.main()
