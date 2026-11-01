import unittest

from portable.state_graph import build_crew_plan, default_crew_definitions


class SoftwareTeamPersonaTests(unittest.TestCase):
    def test_software_company_roles_are_registered_as_bounded_crew_contracts(self):
        crews = {item.crew_id: item for item in default_crew_definitions()}
        expected = {
            "solution_architect", "engineering_manager", "research_evaluation",
            "release_operations", "cto_sponsor", "ceo_sponsor",
        }
        self.assertTrue(expected.issubset(crews))
        for crew_id in expected:
            crew = crews[crew_id]
            self.assertTrue(crew.acceptance_checks)
            self.assertTrue(crew.required_artifacts)
            self.assertTrue(crew.independent_review_required)
            self.assertFalse(set(crew.allowed_authorities) & {"merge", "promote", "self_approve", "bypass_review"})

    def test_manager_and_executive_personas_enter_relevant_graph_plans(self):
        plan = build_crew_plan(
            "Engineering manager coordinate software architecture and capability experiment; CTO technical strategy; CEO investor update",
            graph_digest="graph-test",
            intent_digest="intent-test",
            agents={},
        )
        selected = {item.crew_id for item in plan.assignments}
        self.assertTrue({
            "engineering_manager", "solution_architect", "research_evaluation",
            "cto_sponsor", "ceo_sponsor", "qa_reviewer",
        }.issubset(selected))

    def test_release_operations_is_selected_for_deployment_and_rollback_work(self):
        plan = build_crew_plan(
            "Prepare production deployment, release checklist, rollback rehearsal and observability",
            graph_digest="graph-release",
            intent_digest="intent-release",
            agents={},
        )
        selected = {item.crew_id for item in plan.assignments}
        self.assertIn("release_operations", selected)
        self.assertIn("engineering_manager", selected)


if __name__ == "__main__":
    unittest.main()
