import unittest

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
