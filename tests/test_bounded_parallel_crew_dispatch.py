"""Regression coverage for bounded, proposal-only parallel crew dispatch."""
from __future__ import annotations

import threading
import unittest

from portable.orchestration import Node, NodeKind
from portable.state_graph import GraphAgentTeam, StateGraph


class _ParallelProposal:
    configuration_digest = "parallel-proposal-test-v1"
    implementation_version = "test"

    def __init__(self, barrier: threading.Barrier, lock: threading.Lock, counters: dict[str, int]):
        self.barrier = barrier
        self.lock = lock
        self.counters = counters

    def propose(self, request):
        with self.lock:
            self.counters["active"] += 1
            self.counters["peak"] = max(self.counters["peak"], self.counters["active"])
        try:
            # A barrier makes this test fail quickly if the calls are serialized.
            self.barrier.wait(timeout=3)
            return {
                "schema_version": "auren-crew-proposal-v1",
                "recommendation": f"proposal from {request['crew_id']}",
                "artifacts": list(request["required_artifacts"]),
                "evidence_refs": [f"test://{request['crew_id']}/proposal"],
                "uncertainty": ["Independent review remains required."],
                "blockers": [],
                "acceptance_check_results": {
                    name: {"status": "not_run", "evidence_refs": []}
                    for name in request["acceptance_checks"]
                },
            }
        finally:
            with self.lock:
                self.counters["active"] -= 1


class BoundedParallelCrewDispatchTests(unittest.TestCase):
    def test_independent_proposals_run_concurrently_and_return_stably(self):
        barrier = threading.Barrier(2, timeout=3)
        lock = threading.Lock()
        counters = {"active": 0, "peak": 0}
        agents = {
            crew_id: _ParallelProposal(barrier, lock, counters)
            for crew_id in ("frontend_ui", "backend_api")
        }

        def dispatch(state):
            result = state["auren_crew_dispatch_parallel"](
                {"frontend_ui": "Review accessible UI behavior", "backend_api": "Review API contracts"},
                max_workers=2,
            )
            state["parallel_proposal_results"] = result
            return result

        graph = StateGraph.from_nodes([Node("dispatch", NodeKind.AGENT, dispatch)])
        team = GraphAgentTeam(graph, agents=agents)
        run = team.run("parallel-crew-test", "frontend UI and backend API implementation proposal")

        self.assertEqual(counters["peak"], 2)
        self.assertEqual(run.results["dispatch"].status.value, "passed")
        result = run.results["dispatch"].output
        self.assertEqual(list(result), ["backend_api", "frontend_ui"])
        # Proposal calls remain proposals: they cannot self-approve or authorize execution.
        for receipt in result.values():
            self.assertTrue(receipt["review_required"])
            self.assertEqual(receipt["review_status"], "pending")
            self.assertTrue(receipt["self_attested_acceptance_is_not_authoritative"])


if __name__ == "__main__":
    unittest.main()
