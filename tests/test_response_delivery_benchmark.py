import unittest

from benchmarks.response_delivery_comparison import run_comparison


class ResponseDeliveryBenchmarkTests(unittest.TestCase):
    def test_paired_delivery_benchmark_records_integrity_recovery_and_cost_limits(self):
        report = run_comparison(iterations=1)
        self.assertTrue(report["baseline"]["integrity_ok_all"])
        self.assertTrue(report["candidate"]["integrity_ok_all"])
        self.assertTrue(report["candidate"]["complete_all"])
        self.assertTrue(report["recovery"]["recovery_complete"])
        self.assertFalse(report["recovery"]["automatic_replay_of_acknowledged_chunks"])
        self.assertIsNone(report["cost"]["provider_cost_usd"])
        self.assertGreater(report["cost"]["transported_bytes_proxy"], 0)


if __name__ == "__main__":
    unittest.main()
