import unittest
from portable.benchmark_task_contract import BenchmarkTaskContractFactory
from portable.benchmark_task_dispatch import BenchmarkTaskDispatcher
from portable.benchmark_execution_handshake import BenchmarkExecutionHandshake, derive_runtime_evidence
from portable.benchmark_task_dispatch import BenchmarkExecutionRequest
from pathlib import Path


class BenchmarkExecutionHandshakeTests(unittest.TestCase):
    def setUp(self):
        c = BenchmarkTaskContractFactory().create(domain="research", holdout=True, rationale="gap")
        self.request = BenchmarkTaskDispatcher().dispatch_request(c)

    def test_missing_evidence_fails_closed(self):
        r = BenchmarkExecutionHandshake().complete(self.request, success=True, verified=True)
        self.assertFalse(r.accepted)

    def test_unverified_evidence_fails_closed(self):
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e1"], evidence_kinds=["independent verification evidence"], success=True, verified=False)
        self.assertFalse(r.accepted)


    def test_runtime_adapter_preserves_exact_contract_binding(self):
        from runtime.graph_agent_team import GraphAgentTeam

        class RecordingTeam(GraphAgentTeam):
            def execute(self, **kwargs):
                return kwargs

        team = object.__new__(RecordingTeam)
        result = team.execute_benchmark_request(
            self.request,
            task="bounded benchmark execution",
            memory=Path("."),
            invoke_agent=lambda *args, **kwargs: None,
        )
        self.assertEqual(result["benchmark_domain"], self.request.domain)
        self.assertEqual(result["benchmark_holdout"], self.request.holdout)
        self.assertEqual(result["benchmark_execution_task_id"], self.request.task_id)

    def test_runtime_adapter_rejects_binding_mismatch(self):
        from runtime.graph_agent_team import GraphAgentTeam

        class RecordingTeam(GraphAgentTeam):
            def execute(self, **kwargs):
                return kwargs

        team = object.__new__(RecordingTeam)
        with self.assertRaises(ValueError):
            team.execute_benchmark_request(
                self.request,
                benchmark_domain="different-domain",
            )

    def test_mismatched_execution_request_is_not_implicitly_accepted(self):
        other = BenchmarkTaskContractFactory().create(domain="coding", holdout=True, rationale="other gap")
        other_request = BenchmarkTaskDispatcher().dispatch_request(other)
        self.assertNotEqual(other_request.task_id, self.request.task_id)
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e2","e3","e4"], evidence_kinds=["canonical execution evidence","independent verification evidence","resource and safety evidence"], success=True, verified=True)
        self.assertNotEqual(r.task_id, other_request.task_id)
        self.assertTrue(r.accepted)

    def test_campaign_execution_is_bounded_and_delegates_exact_requests(self):
        from runtime.graph_agent_team import GraphAgentTeam

        class RecordingTeam(GraphAgentTeam):
            def execute_benchmark_request(self, request, **kwargs):
                return request.task_id

        team = object.__new__(RecordingTeam)
        other = BenchmarkTaskDispatcher().dispatch_request(
            BenchmarkTaskContractFactory().create(domain="research", holdout=True, rationale="another gap")
        )
        out = team.execute_benchmark_campaign([self.request, other], max_tasks=2)
        self.assertEqual(out, (self.request.task_id, other.task_id))
        with self.assertRaises(ValueError):
            team.execute_benchmark_campaign([self.request, other], max_tasks=1)

    def test_runtime_evidence_derivation_requires_existing_verifier_and_safety(self):
        class Result:
            def __init__(self, status):
                self.status = status
        ids, kinds, verified = derive_runtime_evidence(
            task_id="benchmark:demo",
            intent_digest="run-1",
            results={"builder": Result("passed"), "verifier": Result("passed")},
            safety_evidence_verified=True,
        )
        self.assertTrue(ids)
        self.assertIn("canonical execution evidence", kinds)
        self.assertIn("independent verification evidence", kinds)
        self.assertIn("resource and safety evidence", kinds)
        self.assertTrue(verified)

        _, _, verified = derive_runtime_evidence(
            task_id="benchmark:demo",
            intent_digest="run-2",
            results={"verifier": Result("passed")},
            safety_evidence_verified=False,
        )
        self.assertFalse(verified)

    def test_incomplete_evidence_coverage_fails_closed(self):
        r = BenchmarkExecutionHandshake().complete(
            self.request,
            evidence_ids=["e1", "e2"],
            evidence_kinds=["canonical execution evidence", "independent verification evidence"],
            success=True,
            verified=True,
        )
        self.assertFalse(r.accepted)
        self.assertIn("required evidence incomplete", r.reason)

    def test_verified_success_is_accepted(self):
        r = BenchmarkExecutionHandshake().complete(self.request, evidence_ids=["e1","e2","e3"], evidence_kinds=["canonical execution evidence","independent verification evidence","resource and safety evidence"], success=True, verified=True)
        self.assertTrue(r.accepted)
        self.assertEqual(r.task_id, self.request.task_id)
        self.assertEqual(r.evidence_ids, ("e1","e2","e3"))

if __name__ == "__main__":
    unittest.main()
