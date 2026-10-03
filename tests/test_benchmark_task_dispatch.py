import unittest
from portable.benchmark_task_contract import BenchmarkTaskContractFactory
from portable.benchmark_task_dispatch import BenchmarkTaskDispatcher


class BenchmarkTaskDispatchTests(unittest.TestCase):
    def test_dispatch_preserves_contract_and_authority_boundary(self):
        c = BenchmarkTaskContractFactory().create(domain="research", holdout=True, rationale="gap")
        r = BenchmarkTaskDispatcher().dispatch_request(c)
        self.assertEqual(r.task_id, c.task_id)
        self.assertEqual(r.domain, "research")
        self.assertEqual(r.authority, "existing-runtime-only")

    def test_dispatch_rejects_excessive_risk(self):
        from portable.benchmark_task_contract import BenchmarkTaskContract
        c = BenchmarkTaskContract("x", "research", True, "x", ("ok",), ("evidence",), risk_budget=.9)
        with self.assertRaises(ValueError):
            BenchmarkTaskDispatcher().dispatch_request(c)

if __name__ == "__main__":
    unittest.main()
