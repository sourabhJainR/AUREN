import unittest

from portable.autonomy_benchmark import AutonomyBenchmarkGate, DIMENSIONS


class AutonomyBenchmarkGateTests(unittest.TestCase):
    def test_benchmark_requires_all_dimensions(self):
        metrics = {name: 1.0 for name in DIMENSIONS[:-1]}
        with self.assertRaises(ValueError):
            AutonomyBenchmarkGate().evaluate(metrics)

    def test_benchmark_gate_requires_every_dimension(self):
        metrics = {name: .9 for name in DIMENSIONS}
        metrics["cross_task_transfer"] = .7
        result = AutonomyBenchmarkGate().evaluate(metrics)
        self.assertFalse(result.gate_passed)
        self.assertIn("cross_task_transfer", result.failed_dimensions)

    def test_benchmark_passes_only_when_all_dimensions_and_overall_pass(self):
        result = AutonomyBenchmarkGate().evaluate({name: .9 for name in DIMENSIONS})
        self.assertTrue(result.gate_passed)
        self.assertEqual(result.overall, .9)


if __name__ == "__main__":
    unittest.main()
