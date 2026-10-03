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

    def test_mismatched_execution_request_is_not_implicitly_accepted(self):
        other = BenchmarkTaskContractFactory().create(domain="coding", holdout=True, rationale="other gap")
        other_request = BenchmarkTaskDispatcher().dispatch_request(other)
        self.assertNotEqual(other_request.task_id, self.request.task_id)
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e2"], success=True, verified=True)
        self.assertNotEqual(r.task_id, other_request.task_id)
        self.assertTrue(r.accepted)

    def test_verified_success_is_accepted(self):
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e1"], success=True, verified=True)
        self.assertTrue(r.accepted)
        self.assertEqual(r.task_id, self.request.task_id)
        self.assertEqual(r.evidence_ids, ("e1",))

if __name__ == "__main__":
    unittest.main()
