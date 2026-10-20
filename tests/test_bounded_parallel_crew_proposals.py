from __future__ import annotations

import threading
import unittest

from portable.orchestration import Node, NodeKind
from portable.state_graph import CrewDefinition, GraphAgentTeam, StateGraph


class _ProposalAgent:
    implementation_version = "test-v1"
    configuration_digest = "test-config"

    def __init__(self, barrier=None):
        self.barrier = barrier

    def propose(self, request):
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        return {
            "schema_version": "auren-crew-proposal-v1",
            "recommendation": "bounded independent proposal",
            "artifacts": ["proposal.json"],
            "evidence_refs": ["test://evidence/proposal"],
            "uncertainty": [],
            "blockers": [],
            "acceptance_check_results": {
                "evidence_found": {
                    "status": "pass",
                    "evidence_refs": ["test://evidence/proposal"],
                }
            },
        }


class _GuardedOnlyAgent:
    def propose_with_execution_guard(self, request, *, execution_guard, execution_guard_persist=None):
        return _ProposalAgent().propose(request)


class BoundedParallelCrewProposalTests(unittest.TestCase):
    def _team(self, agents):
        crews = {
            crew_id: CrewDefinition(
                crew_id=crew_id,
                specialty="independent test proposal",
                triggers=(),
                instructions="Produce a proposal only; do not execute changes.",
                required_artifacts=("proposal.json",),
                allowed_authorities=("read",),
                acceptance_checks=("evidence_found",),
                always_on=True,
                independent_review_required=True,
            )
            for crew_id in ("alpha", "beta")
        }

        def node_run(context):
            return context["auren_crew_dispatch_parallel"](
                {"beta": "inspect beta", "alpha": "inspect alpha"},
                max_workers=2,
            )

        graph = StateGraph.from_nodes([Node("parallel-proposals", NodeKind.AGENT, node_run)])
        return GraphAgentTeam(graph, agents=agents, crews=crews)

    def test_independent_proposals_overlap_and_return_deterministic_order(self):
        barrier = threading.Barrier(2)
        team = self._team({"alpha": _ProposalAgent(barrier), "beta": _ProposalAgent(barrier)})
        run = team.run("parallel-test", "run bounded independent proposals")
        result = run.results["parallel-proposals"].output
        self.assertEqual(["alpha", "beta"], list(result))
        self.assertEqual("pending", result["alpha"]["review_status"])
        self.assertEqual("pending", result["beta"]["review_status"])
        self.assertTrue(result["alpha"]["self_attested_acceptance_is_not_authoritative"])
        self.assertTrue(result["beta"]["self_attested_acceptance_is_not_authoritative"])

    def test_parallel_path_rejects_guarded_only_adapter_before_launch(self):
        team = self._team({"alpha": _GuardedOnlyAgent(), "beta": _ProposalAgent()})
        run = team.run("parallel-guarded-test", "run bounded independent proposals")
        result = run.results["parallel-proposals"]
        self.assertIsNotNone(result.error)
        self.assertIn("side-effect-free propose interface", result.error)

    def test_parallel_worker_limit_is_bounded(self):
        team = self._team({"alpha": _ProposalAgent(), "beta": _ProposalAgent()})

        def invalid_limit(context):
            return context["auren_crew_dispatch_parallel"](
                {"alpha": "inspect alpha", "beta": "inspect beta"}, max_workers=9,
            )

        team.graph = StateGraph.from_nodes([Node("invalid-limit", NodeKind.AGENT, invalid_limit)])
        run = team.run("parallel-limit-test", "run bounded independent proposals")
        self.assertIn("max_workers must be an integer", run.results["invalid-limit"].error)


if __name__ == "__main__":
    unittest.main()
