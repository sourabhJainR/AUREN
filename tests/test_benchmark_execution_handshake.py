import unittest
from portable.benchmark_task_contract import BenchmarkTaskContractFactory
from portable.benchmark_task_dispatch import BenchmarkTaskDispatcher
from portable.benchmark_execution_handshake import BenchmarkExecutionHandshake


class BenchmarkExecutionHandshakeTests(unittest.TestCase):
    def setUp(self):
        c = BenchmarkTaskContractFactory().create(domain="research", holdout=True, rationale="gap")
        self.request = BenchmarkTaskDispatcher().dispatch_request(c)

    def test_missing_evidence_fails_closed(self):
        r = BenchmarkExecutionHandshake().complete(self.request, success=True, verified=True)
        self.assertFalse(r.accepted)

    def test_unverified_evidence_fails_closed(self):
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e1"], success=True, verified=False)
        self.assertFalse(r.accepted)

    def test_verified_success_is_accepted(self):
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e1"], success=True, verified=True)
        self.assertTrue(r.accepted)

if __name__ == "__main__":
    unittest.main()
