from __future__ import annotations

import unittest

from portable.orchestration import Node, NodeKind, RunStatus
from portable.state_graph import GraphAgentTeam, StateGraph
from portable.crew_adapters import (
    ApprovedTool,
    ApprovedToolCrewAdapter,
    ApprovedToolRegistry,
    GraphHandoffController,
    SPECIALIST_CREW_IDS,
    build_specialist_crew_adapters,
    rollback_readiness,
)


def proposal(request):
    return {
        "schema_version": "auren-crew-proposal-v1",
        "recommendation": "review required",
        "artifacts": list(request["required_artifacts"]),
        "evidence_refs": [],
        "uncertainty": ["Independent review is pending."],
        "blockers": [],
        "acceptance_check_results": {
            name: {"status": "not_run", "evidence_refs": []}
            for name in request["acceptance_checks"]
        },
    }


class ApprovedCrewAdapterTests(unittest.TestCase):
    def test_factory_registers_all_core_specialists_with_scoped_read_only_tools(self):
        calls = []
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda args: calls.append(dict(args)) or {"read": True}, "Read-only approved context lookup")
        ])
        planners = {
            crew_id: lambda request: {
                "tool_calls": [{"name": "read_context", "arguments": {"crew_id": request["crew_id"]}}],
                "proposal": proposal(request),
            }
            for crew_id in SPECIALIST_CREW_IDS
        }
        scopes = {crew_id: ("read_context",) for crew_id in SPECIALIST_CREW_IDS}
        adapters = build_specialist_crew_adapters(
            planners=planners, tools=registry, allowed_tools_by_crew=scopes
        )
        self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
        for crew_id, adapter in adapters.items():
            result = adapter.propose({
                "crew_id": crew_id,
                "specialty": crew_id,
                "instructions": "Propose only; do not mutate or self-approve.",
                "task": "Review bounded change",
                "evidence": [],
                "required_artifacts": ["proposal"],
                "acceptance_checks": ["independent review"],
                "allowed_authorities": ["propose_changes"],
                "graph_digest": "graph-digest",
                "crew_plan_digest": "plan-digest",
                "secret": "must not cross boundary",
            })
            self.assertEqual(result["schema_version"], "auren-crew-proposal-v1")
        self.assertEqual(len(calls), len(SPECIALIST_CREW_IDS))
        self.assertEqual({call["crew_id"] for call in calls}, set(SPECIALIST_CREW_IDS))

    def test_configuration_digest_binds_explicit_implementation_version(self):
        registry = ApprovedToolRegistry()
        planner = lambda _request: {"tool_calls": [], "proposal": {}}
        v1 = ApprovedToolCrewAdapter(
            "frontend_ui", planner, registry, allowed_tools=(),
            implementation_version="frontend-planner-v1",
        )
        v2 = ApprovedToolCrewAdapter(
            "frontend_ui", planner, registry, allowed_tools=(),
            implementation_version="frontend-planner-v2",
        )
        self.assertNotEqual(v1.configuration_digest, v2.configuration_digest)

    def test_core_adapter_factory_wires_every_specialist_through_graph_dispatch(self):
        calls = []
        registry = ApprovedToolRegistry([
            ApprovedTool(
                f"read_{crew_id}",
                lambda args, crew_id=crew_id: calls.append((f"read_{crew_id}", dict(args))) or {"read": crew_id},
                f"Read-only context lookup scoped to {crew_id}",
            )
            for crew_id in SPECIALIST_CREW_IDS
        ])
        planners = {
            crew_id: (
                lambda request, crew_id=crew_id: {
                    "tool_calls": [{
                        "name": f"read_{crew_id}",
                        "arguments": {"crew_id": request["crew_id"], "task": request["task"]},
                    }],
                    "proposal": proposal(request),
                }
            )
            for crew_id in SPECIALIST_CREW_IDS
        }
        adapters = build_specialist_crew_adapters(
            planners=planners,
            tools=registry,
            allowed_tools_by_crew={
                crew_id: (f"read_{crew_id}",) for crew_id in SPECIALIST_CREW_IDS
            },
        )
        observed = {}
        intent = (
            "product requirements backend API frontend UI database schema migration "
            "C# dotnet security privacy QA reviewer end user validation"
        )

        def graph_node(state):
            plan_ids = {
                assignment["crew_id"]
                for assignment in state["auren_crew_plan"]["assignments"]
            }
            self.assertTrue(set(SPECIALIST_CREW_IDS) <= plan_ids)
            for crew_id in SPECIALIST_CREW_IDS:
                observed[crew_id] = state["auren_crew_dispatch"](
                    crew_id,
                    f"Exercise {crew_id} through the real graph dispatcher",
                    {"requirement": f"bounded evidence for {crew_id}"},
                )
            return "done"

        graph = StateGraph.from_nodes([
            Node(
                "graph-owned-dispatch",
                NodeKind.DETERMINISTIC,
                graph_node,
                evaluator=lambda output: output == "done",
            )
        ])
        result = GraphAgentTeam(graph, agents=adapters).run(
            "crew-integration-all-specialists",
            intent,
            {"secret": "must not be forwarded implicitly"},
        )

        self.assertEqual(result.status, RunStatus.ACCEPTED)
        self.assertEqual(set(observed), set(SPECIALIST_CREW_IDS))
        for crew_id in SPECIALIST_CREW_IDS:
            dispatched = observed[crew_id]
            self.assertEqual(dispatched["crew_id"], crew_id)
            self.assertTrue(dispatched["review_required"])
            self.assertEqual(dispatched["review_status"], "pending")
            self.assertTrue(dispatched["self_attested_acceptance_is_not_authoritative"])
        self.assertEqual({tool_name for tool_name, _args in calls}, {
            f"read_{crew_id}" for crew_id in SPECIALIST_CREW_IDS
        })
        self.assertEqual(len(calls), len(SPECIALIST_CREW_IDS))
        self.assertEqual({args["crew_id"] for _tool_name, args in calls}, set(SPECIALIST_CREW_IDS))
        self.assertTrue(all("secret" not in args for _tool_name, args in calls))

    def test_tool_scope_changes_change_stable_graph_crew_plan_digest(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda _args: {}, "Read-only task context"),
            ApprovedTool("read_docs", lambda _args: {}, "Read-only documentation"),
        ])
        planners = {
            crew_id: lambda request: {"tool_calls": [], "proposal": proposal(request)}
            for crew_id in SPECIALIST_CREW_IDS
        }
        scopes_a = {crew_id: ("read_context",) for crew_id in SPECIALIST_CREW_IDS}
        scopes_b = dict(scopes_a)
        scopes_b["frontend_ui"] = ("read_docs",)
        graph = StateGraph.from_nodes([
            Node("work", NodeKind.DETERMINISTIC, lambda _state: "done", evaluator=lambda output: output == "done")
        ])
        team_a = GraphAgentTeam(graph, agents=build_specialist_crew_adapters(
            planners=planners, tools=registry, allowed_tools_by_crew=scopes_a
        ))
        team_b = GraphAgentTeam(graph, agents=build_specialist_crew_adapters(
            planners=planners, tools=registry, allowed_tools_by_crew=scopes_b
        ))
        plan_a = team_a.plan_crews("frontend UI")
        plan_b = team_b.plan_crews("frontend UI")
        self.assertNotEqual(plan_a.digest, plan_b.digest)
        frontend_a = next(item for item in plan_a.assignments if item.crew_id == "frontend_ui")
        frontend_b = next(item for item in plan_b.assignments if item.crew_id == "frontend_ui")
        self.assertNotEqual(frontend_a.agent_config_digest, frontend_b.agent_config_digest)

    def test_factory_fails_closed_when_a_core_planner_is_missing(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda _args: {}, "Read-only context lookup")
        ])
        planners = {crew_id: (lambda request: {"tool_calls": [], "proposal": proposal(request)})
                    for crew_id in SPECIALIST_CREW_IDS[:-1]}
        scopes = {crew_id: ("read_context",) for crew_id in SPECIALIST_CREW_IDS}
        with self.assertRaisesRegex(ValueError, "missing specialist proposal planners"):
            build_specialist_crew_adapters(
                planners=planners, tools=registry, allowed_tools_by_crew=scopes
            )

    def test_adapter_invokes_only_explicit_read_only_tools(self):
        calls = []
        registry = ApprovedToolRegistry([
            ApprovedTool("inspect_diff", lambda args: calls.append(dict(args)) or {"files": 2}, "Read-only diff inspection"),
            ApprovedTool("search_docs", lambda args: {"matches": []}, "Read-only documentation search"),
        ])
        adapter = ApprovedToolCrewAdapter(
            "qa_reviewer",
            lambda request: {
                "tool_calls": [{"name": "inspect_diff", "arguments": {"path": "src/"}}],
                "proposal": proposal(request),
            },
            registry,
            allowed_tools=("inspect_diff",),
        )
        request = {
            "crew_id": "qa_reviewer", "task": "Review change",
            "required_artifacts": ["review findings"], "acceptance_checks": ["tests are tied to requirements"],
            "secret": "must not cross boundary",
        }
        result = adapter.propose(request)
        self.assertEqual(result["schema_version"], "auren-crew-proposal-v1")
        self.assertEqual(calls, [{"path": "src/"}])

    def test_adapter_rejects_unapproved_tool_and_side_effecting_registry(self):
        registry = ApprovedToolRegistry([ApprovedTool("read", lambda _args: {}, "Read-only")])
        adapter = ApprovedToolCrewAdapter(
            "python", lambda request: {
                "tool_calls": [{"name": "write_file", "arguments": {}}],
                "proposal": proposal(request),
            }, registry, allowed_tools=("read",),
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            adapter.propose({
                "crew_id": "python", "required_artifacts": [], "acceptance_checks": [],
            })
        with self.assertRaisesRegex(ValueError, "side-effecting"):
            ApprovedToolRegistry([ApprovedTool("merge", lambda _args: {}, "Mutation", side_effecting=True)])

    def test_tool_count_and_output_are_bounded(self):
        registry = ApprovedToolRegistry([ApprovedTool("read", lambda _args: object(), "Read-only")])
        adapter = ApprovedToolCrewAdapter(
            "python", lambda request: {
                "tool_calls": [{"name": "read", "arguments": {}}],
                "proposal": proposal(request),
            }, registry, allowed_tools=("read",), max_tool_calls=1,
        )
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            adapter.propose({"crew_id": "python", "required_artifacts": [], "acceptance_checks": []})
        with self.assertRaisesRegex(ValueError, "max_tool_calls"):
            ApprovedToolCrewAdapter("python", lambda _r: {}, registry, allowed_tools=("read",), max_tool_calls=99)

    def test_handoffs_require_contract_and_graph_assignment(self):
        controller = GraphHandoffController(max_attempts=2)
        with self.assertRaisesRegex(PermissionError, "not declared"):
            controller.enqueue(
                source_crew="frontend_ui", target_crew="security_privacy", task="Review auth",
                source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
                selected_crews=("frontend_ui", "security_privacy"),
            )
        with self.assertRaisesRegex(PermissionError, "not assigned"):
            controller.enqueue(
                source_crew="frontend_ui", target_crew="qa_reviewer", task="Review UI",
                source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
                selected_crews=("frontend_ui",),
            )
        handoff = controller.enqueue(
            source_crew="frontend_ui", target_crew="qa_reviewer", task="Review UI",
            source_plan_digest="abc", allowed_handoffs=("qa_reviewer",),
            selected_crews=("frontend_ui", "qa_reviewer"),
        )
        with self.assertRaisesRegex(RuntimeError, "active graph node"):
            controller.claim_next(graph_node_active=False)
        claimed = controller.claim_next(graph_node_active=True)
        self.assertEqual(claimed, handoff)
        retry = controller.retry(claimed, retryable=True)
        self.assertEqual(retry.attempt, 1)
        self.assertIsNone(controller.retry(retry, retryable=True))
        with self.assertRaisesRegex(PermissionError, "independent review"):
            controller.mark_completed(handoff, independent_review_passed=False)
        controller.mark_completed(handoff, independent_review_passed=True)

    def test_handoff_checkpoint_resumes_queued_work(self):
        controller = GraphHandoffController(max_attempts=3)
        controller.enqueue(
            source_crew="backend_api", target_crew="qa_reviewer", task="Review endpoint",
            source_plan_digest="plan-digest", allowed_handoffs=("qa_reviewer",),
            selected_crews=("backend_api", "qa_reviewer"),
        )
        resumed = GraphHandoffController.resume(controller.snapshot())
        claimed = resumed.claim_next(graph_node_active=True)
        self.assertEqual(claimed.target_crew, "qa_reviewer")
        self.assertEqual(claimed.task, "Review endpoint")

    def test_stategraph_controls_handoff_retry_resume_and_rollback_gate(self):
        observed = {}
        def run_node(state):
            observed["enqueue"] = state["auren_crew_handoff_enqueue"](
                "frontend_ui", "qa_reviewer", "Review accessible UI"
            )
            observed["claimed"] = state["auren_crew_handoff_claim"]()
            observed["retry"] = state["auren_crew_handoff_retry"](observed["claimed"], True)
            observed["checkpoint"] = state["auren_crew_handoff_checkpoint"]()
            observed["readiness"] = state["auren_crew_rollback_readiness"](
                consequential=True, last_known_good_digest=None, rollback_procedure_ref=None,
                independent_review_passed=False, required_evidence_refs=("ci://main",),
                verified_evidence_refs=(),
            )
            return "recorded"
        graph = StateGraph.from_nodes([
            Node("supervised", NodeKind.DETERMINISTIC, run_node, evaluator=lambda output: output == "recorded")
        ])
        team = GraphAgentTeam(graph)
        result = team.run("handoff-1", "frontend UI")
        self.assertEqual(result.status, RunStatus.ACCEPTED)
        self.assertEqual(observed["claimed"]["target_crew"], "qa_reviewer")
        self.assertEqual(observed["retry"]["attempt"], 1)
        self.assertEqual(observed["checkpoint"]["queued"][0]["attempt"], 1)
        self.assertFalse(observed["readiness"]["ready"])
    def test_stategraph_rejects_handoff_from_unassigned_source(self):
        def run_node(state):
            with self.assertRaisesRegex(ValueError, "not assigned"):
                state["auren_crew_handoff_enqueue"]("python", "qa_reviewer", "Review code")
            return "recorded"
        graph = StateGraph.from_nodes([
            Node("supervised", NodeKind.DETERMINISTIC, run_node, evaluator=lambda output: output == "recorded")
        ])
        result = GraphAgentTeam(graph).run("handoff-2", "frontend UI")
        self.assertEqual(result.status, RunStatus.ACCEPTED)

    def test_rollback_readiness_fails_closed(self):
        blocked = rollback_readiness(
            consequential=True, last_known_good_digest=None, rollback_procedure_ref=None,
            independent_review_passed=False, required_evidence_refs=("test://ci", "attest://run"),
            verified_evidence_refs=("test://ci",),
        )
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["missing_evidence_refs"], ["attest://run"])
        ready = rollback_readiness(
            consequential=True, last_known_good_digest="sha256:known-good",
            rollback_procedure_ref="runbook://rollback", independent_review_passed=True,
            required_evidence_refs=("test://ci",), verified_evidence_refs=("test://ci",),
        )
        self.assertTrue(ready["ready"])


if __name__ == "__main__":
    unittest.main()
