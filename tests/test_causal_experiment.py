import unittest
from portable.causal_experiment import CausalExperimentSelector, CausalHypothesis

class CausalExperimentTests(unittest.TestCase):
    def test_selects_high_information_safe_hypothesis(self):
        result=CausalExperimentSelector().select(hypotheses=(
            CausalHypothesis("low-confidence","probe-a","effect-a",.30,2,.90),
            CausalHypothesis("high-confidence","probe-b","effect-b",.95,8,.20),
        ),risk_budget=.50)
        self.assertIsNotNone(result)
        self.assertEqual(result.hypothesis,"low-confidence")
        self.assertGreater(result.information_gain,result.risk)

    def test_respects_risk_budget(self):
        result=CausalExperimentSelector().select(
            hypotheses=(CausalHypothesis("risky","act","effect",.0,0,.9),),
            risk_budget=.40)
        self.assertIsNone(result)

if __name__=="__main__":
    unittest.main()
