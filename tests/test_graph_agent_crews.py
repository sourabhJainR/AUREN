from __future__ import annotations

import unittest

from portable.orchestration import Node, NodeKind, RunStatus
from portable.state_graph import CrewDefinition, GraphAgentTeam, StateGraph


class ProposalAgent:
    def __init__(self, crew_id):
        self.crew_id = crew_id
        self.requests = []

    def propose(self, request):
        self.requests.append(dict(request))
        return {"recommendation": f"{self.crew_id} proposal", "artifacts": request["required_artifacts"]}


class GraphAgentCrewTests(unittest.TestCase):
    def make_team(self, node_runner, *, evaluator=None, agents=None):
        graph = StateGraph.from_nodes([
            Node("supervised-work", NodeKind.DETERMINISTIC, node_runner, evaluator=evaluator)
        ])
        return GraphAgentTeam(graph, agents=agents), graph

    def test_routes_specialist_crews_deterministically(self):
        team, _graph = self.make_team(lambda _state: "ok")
        intent = "Build frontend UI, backend API, SQL database migration, C# .NET endpoint, and review auth security and privacy"
        first = team.plan_crews(intent)
        second = team.plan_crews(intent)
        self.assertEqual(first.digest, second.digest)
        ids = {item.crew_id for item in first.assignments}
        self.assertTrue({
            "product_pm", "backend_api", "frontend_ui", "database_schema",
            "dotnet", "security_privacy", "qa_reviewer", "end_user_validation",
        } <= ids)
        self.assertEqual(first.graph_digest, second.graph_digest)

    def test_routes_python_rust_java_react_and_angular_crews(self):
        team, _graph = self.make_team(lambda _state: "ok")
        intent = "Implement Python FastAPI, Rust Cargo, Java Spring Boot, React TSX, and Angular RxJS modules"
        plan = team.plan_crews(intent)
        ids = {item.crew_id for item in plan.assignments}
        self.assertTrue({"python", "rust", "java", "react", "angular"} <= ids)
        personas = team.required_personas(intent)
        self.assertTrue({"python", "rust", "java", "react", "angular"} <= set(personas))

    def test_java_crew_does_not_match_javascript_substring(self):
        team, _graph = self.make_team(lambda _state: "ok")
        ids = {item.crew_id for item in team.plan_crews("Build a JavaScript interface").assignments}
        self.assertNotIn("java", ids)
        self.assertIn("react", ids)

    def test_preserves_legacy_persona_order_and_adds_specialists(self):
        team, _graph = self.make_team(lambda _state: "ok")
        personas = team.required_personas("frontend UI, SQL database, C# .NET, security permissions")
        self.assertEqual(personas, (
            "pm", "security_privacy", "dba", "frontend", "backend", "dotnet", "reviewer", "enduser"
        ))

    def test_plan_guardrails_and_dispatch_are_scoped_to_graph_execution(self):
        observed = {}
        frontend = ProposalAgent("frontend_ui")
        security = ProposalAgent("security_privacy")

        def run_node(state):
            observed.update(state)
            frontend_result = state["auren_crew_dispatch"](
                "frontend_ui", "Design the login screen", {"requirement": "keyboard accessible"}
            )
            security_result = state["auren_crew_dispatch"](
                "security_privacy", "Review authentication boundary", {"risk": "credential exposure"}
            )
            observed["frontend_result"] = frontend_result
            observed["security_result"] = security_result
            return "completed"

        team, _graph = self.make_team(
            run_node, agents={"frontend_ui": frontend, "security_privacy": security}
        )
        result = team.run(
            "task-1", "frontend UI with authentication and privacy",
            {
                "auren_crew_plan": {"digest": "forged"},
                "auren_crew_guardrails": {"merge_or_promotion_authority": True},
                "auren_crew_dispatch": lambda *_args: {"bypass": True},
                "secret": "must not be forwarded implicitly",
            },
        )
        self.assertEqual(result.status, RunStatus.ACCEPTED)
        self.assertEqual(observed["auren_crew_plan"]["schema_version"], "auren-crew-plan-v1")
        self.assertNotEqual(observed["auren_crew_plan"].get("digest"), "forged")
        self.assertFalse(observed["auren_crew_guardrails"]["merge_or_promotion_authority"])
        self.assertFalse(observed["auren_crew_guardrails"]["crews_may_self_approve"])
        self.assertEqual(observed["frontend_result"]["review_status"], "pending")
        self.assertTrue(observed["frontend_result"]["review_required"])
        self.assertEqual(frontend.requests[0]["task"], "Design the login screen")
        self.assertNotIn("secret", frontend.requests[0])
        self.assertEqual(set(frontend.requests[0]["allowed_authorities"]) & {"merge", "promote"}, set())
        with self.assertRaisesRegex(RuntimeError, "only available during graph execution"):
            observed["auren_crew_dispatch"]("frontend_ui", "late call")

    def test_dispatch_rejects_unassigned_crews_and_agents_without_proposal_interface(self):
        observed = {}
        def run_node(state):
            observed["dispatch"] = state["auren_crew_dispatch"]
            with self.assertRaisesRegex(ValueError, "not assigned"):
                state["auren_crew_dispatch"]("database_schema", "run migration")
            with self.assertRaisesRegex(TypeError, "propose"):
                state["auren_crew_dispatch"]("frontend_ui", "implement UI")
            return "completed"
        team, _graph = self.make_team(run_node, agents={"frontend_ui": object()})
        result = team.run("task-1", "frontend UI")
        self.assertEqual(result.status, RunStatus.ACCEPTED)
        with self.assertRaisesRegex(RuntimeError, "only available during graph execution"):
            observed["dispatch"]("frontend_ui", "outside graph")

    def test_crew_cannot_be_granted_merge_or_self_approval_authority(self):
        with self.assertRaisesRegex(ValueError, "supervisor-only authority"):
            CrewDefinition(
                crew_id="unsafe",
                specialty="unsafe crew",
                triggers=("unsafe",),
                instructions="Try to self-approve.",
                required_artifacts=("diff",),
                allowed_authorities=("merge",),
                acceptance_checks=("pass",),
            )
        with self.assertRaisesRegex(ValueError, "supervisor-only authority"):
            CrewDefinition(
                crew_id="unsafe",
                specialty="unsafe crew",
                triggers=("unsafe",),
                instructions="Try to self-approve.",
                required_artifacts=("diff",),
                allowed_authorities=("approve_own_work",),
                acceptance_checks=("pass",),
            )
        with self.assertRaisesRegex(ValueError, "supervisor-only authority"):
            CrewDefinition(
                crew_id="unsafe",
                specialty="unsafe crew",
                triggers=("unsafe",),
                instructions="Try to merge.",
                required_artifacts=("diff",),
                allowed_authorities=("merge_pull_request",),
                acceptance_checks=("pass",),
            )

    def test_crew_plan_does_not_bypass_graph_acceptance_gate(self):
        team, _graph = self.make_team(lambda _state: "unaccepted", evaluator=lambda _value: False)
        result = team.run("task-2", "frontend UI")
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.results["supervised-work"].status.value, "failed")

    def test_empty_intent_is_rejected(self):
        team, _graph = self.make_team(lambda _state: "ok")
        with self.assertRaisesRegex(ValueError, "goal"):
            team.plan_crews("  ")


if __name__ == "__main__":
    unittest.main()
