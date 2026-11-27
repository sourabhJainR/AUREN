import unittest

from portable.optimizer_rubric_campaign import PairedTrial, analyze_campaign


def run():
    return dict(
        task_digest="t", source_digest="s", context_digest="c", tool_digest="x",
        provider_version="v", model_tier="standard", model_id="m",
        rubric_digest="r", policy_digest="p", privacy_scope_digest="q",
        acceptance_digest="a", context_budget_tokens=1000, input_tokens=100,
        output_tokens=20, latency_ms=100, cost_usd=.01, policy_violations=0,
    )


def receipt(score):
    return {"status": "passed", "weighted_score": score, "task_digest": "t"}


class TestCampaign(unittest.TestCase):
    def test_savings(self):
        baseline = run()
        optimized = dict(baseline, input_tokens=70, latency_ms=80, cost_usd=.006)
        result = analyze_campaign([PairedTrial("1", baseline, optimized, receipt(90), receipt(91))])
        self.assertEqual(30, result["metrics"]["input_tokens"]["mean"])
        self.assertTrue(result["promotion_eligible"])

    def test_violation_blocks(self):
        baseline = run()
        optimized = dict(baseline, policy_violations=1)
        result = analyze_campaign([PairedTrial("1", baseline, optimized, receipt(90), receipt(100))])
        self.assertFalse(result["promotion_eligible"])
        self.assertIn("policy_violations_increased", result["eligibility_reasons"])

    def test_identity_mismatch(self):
        baseline = run()
        optimized = dict(baseline, model_id="other")
        with self.assertRaises(ValueError):
            analyze_campaign([PairedTrial("1", baseline, optimized, receipt(90), receipt(90))])

    def test_unscored_receipt(self):
        baseline = run()
        with self.assertRaises(ValueError):
            analyze_campaign([
                PairedTrial("1", baseline, baseline, receipt(90), dict(receipt(90), status="unscored"))
            ])

    def test_per_trial_regression_cannot_be_hidden_by_average(self):
        baseline_a, baseline_b = run(), run()
        optimized_a, optimized_b = dict(baseline_a), dict(baseline_b)
        trials = [
            PairedTrial("a", baseline_a, optimized_a, receipt(90), receipt(50)),
            PairedTrial("b", baseline_b, optimized_b, receipt(80), receipt(100)),
        ]
        result = analyze_campaign(trials, max_quality_regression=10)
        self.assertEqual(-10, result["metrics"]["quality_delta"]["mean"])
        self.assertFalse(result["promotion_eligible"])
        self.assertIn("per_trial_quality_regression_exceeded", result["eligibility_reasons"])

    def test_quality_floor_is_enforced(self):
        baseline, optimized = run(), run()
        result = analyze_campaign(
            [PairedTrial("1", baseline, optimized, receipt(90), receipt(49))],
            minimum_quality=50,
        )
        self.assertFalse(result["promotion_eligible"])
        self.assertIn("optimized_quality_below_floor", result["eligibility_reasons"])

    def test_thresholds_are_bounded_and_finite(self):
        baseline, optimized = run(), run()
        trial = PairedTrial("1", baseline, optimized, receipt(90), receipt(90))
        for kwargs in ({"minimum_quality": 101}, {"max_quality_regression": -1}, {"minimum_quality": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze_campaign([trial], **kwargs)

    def test_receipt_task_digest_mismatch(self):
        baseline, optimized = run(), run()
        with self.assertRaisesRegex(ValueError, "task mismatch"):
            analyze_campaign([
                PairedTrial("1", baseline, optimized, receipt(90), dict(receipt(90), task_digest="other"))
            ])

    def test_trial_ids_must_be_unique(self):
        baseline, optimized = run(), run()
        trial = PairedTrial("duplicate", baseline, optimized, receipt(90), receipt(90))
        with self.assertRaisesRegex(ValueError, "unique"):
            analyze_campaign([trial, trial])


if __name__ == "__main__":
    unittest.main()
