import unittest

from portable.orchestration import Node, NodeKind
from portable.state_graph import GraphAgentTeam, StateGraph
from portable.crew_adapters import (
    ApprovedTool, ApprovedToolRegistry, SOFTWARE_TEAM_PERSONA_IDS,
    build_software_team_persona_adapters,
)


class SoftwareTeamPersonaAdapterTests(unittest.TestCase):
    def test_builds_all_six_persona_adapters_with_explicit_read_only_scopes(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda _args: {"ok": True}, "Read-only task context"),
        ])
        planners = {persona: (lambda _request: {}) for persona in SOFTWARE_TEAM_PERSONA_IDS}
        scopes = {persona: ("read_context",) for persona in SOFTWARE_TEAM_PERSONA_IDS}
        adapters = build_software_team_persona_adapters(
            planners=planners, tools=registry, allowed_tools_by_persona=scopes,
            implementation_versions={persona: "test-1" for persona in SOFTWARE_TEAM_PERSONA_IDS},
        )
        self.assertEqual(set(SOFTWARE_TEAM_PERSONA_IDS), set(adapters))
        for persona, adapter in adapters.items():
            self.assertEqual(persona, adapter.crew_id)
            self.assertEqual(frozenset({"read_context"}), adapter._allowed_tools)
            self.assertTrue(adapter.configuration_digest)


    def test_graph_agent_team_registers_all_personas_and_marks_them_available(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda _args: {"ok": True}, "Read-only task context"),
        ])
        planners = {persona: (lambda _request: {}) for persona in SOFTWARE_TEAM_PERSONA_IDS}
        scopes = {persona: ("read_context",) for persona in SOFTWARE_TEAM_PERSONA_IDS}
        adapters = build_software_team_persona_adapters(
            planners=planners, tools=registry, allowed_tools_by_persona=scopes,
        )
        graph = StateGraph.from_nodes([
            Node("team-registration", NodeKind.DETERMINISTIC, lambda _state: "ok")
        ])
        team = GraphAgentTeam(graph)
        registered = team.register_software_team_personas(adapters)
        self.assertEqual(SOFTWARE_TEAM_PERSONA_IDS, registered)
        plan = team.plan_crews(
            "Engineering manager coordinate software architecture, capability experiment, production release, CTO technical strategy, CEO investor update"
        )
        selected = {item.crew_id: item.agent_registered for item in plan.assignments}
        self.assertTrue(all(selected.get(persona_id, False) for persona_id in SOFTWARE_TEAM_PERSONA_IDS))

    def test_graph_agent_team_rejects_partial_persona_registration(self):
        graph = StateGraph.from_nodes([
            Node("team-registration", NodeKind.DETERMINISTIC, lambda _state: "ok")
        ])
        team = GraphAgentTeam(graph)
        with self.assertRaisesRegex(ValueError, "registration must be complete"):
            team.register_software_team_personas({})


    def test_missing_persona_planner_fails_closed(self):
        registry = ApprovedToolRegistry([
            ApprovedTool("read_context", lambda _args: {}, "Read-only task context"),
        ])
        planners = {persona: (lambda _request: {}) for persona in SOFTWARE_TEAM_PERSONA_IDS[:-1]}
        scopes = {persona: ("read_context",) for persona in SOFTWARE_TEAM_PERSONA_IDS}
        with self.assertRaisesRegex(ValueError, "missing software-team persona planners"):
            build_software_team_persona_adapters(
                planners=planners, tools=registry, allowed_tools_by_persona=scopes,
            )


if __name__ == "__main__":
    unittest.main()
