import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from portable.execution_strategy_learning import ExecutionStrategyLearner
from portable.strategy_canary import StrategyCanaryController

class StrategyCanaryTests(unittest.TestCase):
    def _seed(self,root):
        l=ExecutionStrategyLearner(root)
        for i in range(5):
            l.record(role="team",task="artifact",strategy="evidence-first",outcome="passed",
                     evidence_quality=.9,cost_score=.2,duration_seconds=2,verification="deep",
                     retry="stop",evidence_ids=[f"e:{i}"])
    def test_requires_canary_before_promotion(self):
        with TemporaryDirectory() as d:
            r=Path(d); self._seed(r); c=StrategyCanaryController(r)
            self.assertEqual(c.evaluate(role="team",task="artifact",strategy="evidence-first").state,"canary")
            self.assertEqual(c.evaluate(role="team",task="artifact",strategy="evidence-first",canary_passed=True).state,"promoted")
    def test_failed_gate_rolls_back(self):
        with TemporaryDirectory() as d:
            r=Path(d); self._seed(r)
            self.assertEqual(StrategyCanaryController(r,min_evidence=.99).evaluate(
                role="team",task="artifact",strategy="evidence-first",canary_passed=True).state,"rollback")

if __name__=="__main__": unittest.main()
