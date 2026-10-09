import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.autonomy_benchmark import AutonomyBenchmarkGate
from portable.autonomy_benchmark_history import AutonomyBenchmarkHistory


class BenchmarkHistoryTests(unittest.TestCase):
    def _benchmark(self, value):
        return AutonomyBenchmarkGate().evaluate({name: value for name in (
            "goal_completion","cross_task_transfer","self_model_calibration",
            "causal_learning","safe_autonomy","resource_efficiency")})

    def test_history_records_and_trends(self):
        with TemporaryDirectory() as td:
            history=AutonomyBenchmarkHistory(Path(td))
            for i, value in enumerate((.9, .89, .7)):
                history.record(self._benchmark(value), task=f"task-{i}",
                               evidence_ids=[f"evidence:{i}"])
                if i < 2:
                    time.sleep(1.01)
            trend=history.trend()
            self.assertEqual(trend.samples, 3)
            self.assertLess(trend.delta, 0)
            self.assertTrue(trend.regressed)

    def test_empty_history_is_safe(self):
        with TemporaryDirectory() as td:
            trend=AutonomyBenchmarkHistory(Path(td)).trend()
            self.assertEqual(trend.samples,0)
            self.assertFalse(trend.regressed)


if __name__=="__main__":
    unittest.main()
