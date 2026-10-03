import unittest
from portable.benchmark_task_contract import BenchmarkTaskContractFactory


class BenchmarkTaskContractTests(unittest.TestCase):
    def test_deterministic_contract(self):
        f = BenchmarkTaskContractFactory()
        a = f.create(domain="research", holdout=True, rationale="missing holdout")
        b = f.create(domain="research", holdout=True, rationale="missing holdout")
        self.assertEqual(a, b)
        self.assertTrue(a.holdout)
        self.assertEqual(len(a.evidence_requirements), 3)

    def test_invalid_budget_fails_closed(self):
        from portable.benchmark_task_contract import BenchmarkTaskContract
        with self.assertRaises(ValueError):
            BenchmarkTaskContract("x", "research", True, "x", ("ok",), ("evidence",), risk_budget=2)

if __name__ == "__main__":
    unittest.main()
