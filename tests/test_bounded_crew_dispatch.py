from __future__ import annotations

import threading
import time
import unittest

from portable.bounded_crew_dispatch import BoundedCrewDispatcher
from portable.orchestration import Node, NodeKind
from portable.state_graph import CrewDefinition, GraphAgentTeam, StateGraph


class BoundedCrewDispatcherTests(unittest.TestCase):
    def test_parallel_execution_is_bounded_and_results_keep_input_order(self):
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        def dispatch(request):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                # Deliberately complete later input items first.
                time.sleep(0.02 * (5 - int(request["task"])))
                return {"crew_id": request["crew_id"], "task": request["task"]}
            finally:
                with lock:
                    active -= 1

        requests = [
            {"crew_id": f"crew-{i}", "task": str(i), "evidence": {}}
            for i in range(1, 5)
        ]
        results = BoundedCrewDispatcher(dispatch).run(requests, max_workers=2)

        self.assertEqual([request["task"] for request in requests], [item["task"] for item in results])
        self.assertLessEqual(maximum_active, 2)
        self.assertGreaterEqual(maximum_active, 2)

    def test_empty_batch_returns_empty_without_dispatch(self):
        dispatcher = BoundedCrewDispatcher(lambda _request: self.fail("must not dispatch"))
        self.assertEqual([], dispatcher.run([]))

    def test_batch_size_and_worker_budget_are_bounded(self):
        dispatcher = BoundedCrewDispatcher(lambda request: request["task"])
        requests = [{"crew_id": "crew", "task": str(i)} for i in range(9)]
        with self.assertRaisesRegex(ValueError, "8 request limit"):
            dispatcher.run(requests)
        with self.assertRaisesRegex(ValueError, "between 1 and 4"):
            dispatcher.run([{"crew_id": "crew", "task": "one"}], max_workers=5)
        with self.assertRaisesRegex(ValueError, "between 1 and 4"):
            dispatcher.run([{"crew_id": "crew", "task": "one"}], max_workers=True)

    def test_invalid_request_fails_before_any_dispatch(self):
        called = []
        dispatcher = BoundedCrewDispatcher(lambda request: called.append(request))
        with self.assertRaisesRegex(ValueError, "requires crew_id"):
            dispatcher.run([{"task": "missing crew"}])
        self.assertEqual([], called)

    def test_dispatch_failure_is_not_converted_to_success(self):
        def dispatch(request):
            if request["task"] == "bad":
                raise RuntimeError("provider failure")
            return {"accepted": True}

        dispatcher = BoundedCrewDispatcher(dispatch)
        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            dispatcher.run([
                {"crew_id": "crew-a", "task": "good"},
                {"crew_id": "crew-b", "task": "bad"},
            ], max_workers=2)


    def test_graph_scoped_batch_dispatch_integrates_with_stategraph(self):
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        class ProposalAgent:
            configuration_digest = "test-config"
            implementation_version = "test-v1"

            def __init__(self, crew_id):
                self.crew_id = crew_id

            def propose(self, request):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    time.sleep(0.03)
                    definition = crews[self.crew_id]
                    return {
                        "schema_version": "auren-crew-proposal-v1",
                        "recommendation": f"proposal from {self.crew_id}",
                        "artifacts": list(definition.required_artifacts),
                        "evidence_refs": ["test://proposal/evidence"],
                        "uncertainty": ["Independent review remains required."],
                        "blockers": [],
                        "acceptance_check_results": {
                            name: {"status": "not_run", "evidence_refs": []}
                            for name in definition.acceptance_checks
                        },
                    }
                finally:
                    with lock:
                        active -= 1

        crews = {
            crew_id: CrewDefinition(
                crew_id, "test specialty", (), "propose only",
                ("proposal",), ("propose",), ("proposal is structured",), (), True,
            )
            for crew_id in ("crew_a", "crew_b")
        }
        agents = {crew_id: ProposalAgent(crew_id) for crew_id in crews}

        def graph_node(state):
            results = state["auren_crew_dispatch_batch"](
                [
                    {"crew_id": "crew_a", "task": "first proposal", "evidence": {}},
                    {"crew_id": "crew_b", "task": "second proposal", "evidence": {}},
                ],
                max_workers=2,
            )
            return {"batch_results": results}

        graph = StateGraph.from_nodes([Node("dispatch", NodeKind.DETERMINISTIC, graph_node)])
        team = GraphAgentTeam(graph, agents=agents, crews=crews)
        result = team.run("bounded-batch-integration", "parallel crew proposal test")

        self.assertEqual("accepted", result.status.value)
        proposals = result.results["dispatch"].output["batch_results"]
        self.assertEqual(["crew_a", "crew_b"], [item["crew_id"] for item in proposals])
        self.assertTrue(all(item["review_required"] for item in proposals))
        self.assertLessEqual(maximum_active, 2)
        self.assertGreaterEqual(maximum_active, 2)


if __name__ == "__main__":
    unittest.main()
