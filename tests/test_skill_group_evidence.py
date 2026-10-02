import unittest

from portable.skill_evidence import SkillExecutionEvidence
from portable.skill_group_evidence import adapt_execution_groups, attribute_groups, group_key


class SkillGroupEvidenceTests(unittest.TestCase):
    def test_group_key_is_deterministic(self):
        self.assertEqual(group_key(("reviewer", "planner")), "planner|reviewer")

    def test_attribute_groups_uses_group_member_evidence(self):
        rows = (
            SkillExecutionEvidence("planner", "skill", "planning", 1, "passed", 0.8, 0, 1, 0.3),
            SkillExecutionEvidence("reviewer", "skill", "review", 1, "passed", 0.9, 2, 2, 0.8),
        )
        groups = attribute_groups(skill_evidence=rows, execution_groups=(("planner", "reviewer"),))
        self.assertEqual(groups[0].key, "planner|reviewer")
        self.assertGreater(groups[0].useful_evidence, 0.5)
        self.assertEqual(groups[0].samples, 1)

    def test_weak_group_replaces_low_contributor(self):
        class O:
            def __init__(self, name, phase):
                self.name, self.phase, self.instructions, self.estimated_cost = name, phase, "x", 0.2

        options = (O("planner", "planning"), O("reviewer", "review"), O("researcher", "research"))
        groups, changes = adapt_execution_groups(
            execution_groups=(("planner", "reviewer"),),
            options=options,
            group_history={"planner|reviewer": {"samples": 4, "useful_evidence": 0.2}},
            contribution_history={
                "planner": {"evidence_quality": 0.2},
                "reviewer": {"evidence_quality": 0.25},
                "researcher": {"evidence_quality": 0.9},
            },
        )
        self.assertEqual(groups, (("researcher", "reviewer"),))
        self.assertEqual(changes[0].action, "replace")

    def test_strong_group_can_add_complementary_skill(self):
        class O:
            def __init__(self, name, phase):
                self.name, self.phase, self.instructions, self.estimated_cost = name, phase, "x", 0.2

        options = (O("planner", "planning"), O("reviewer", "review"), O("researcher", "research"))
        groups, changes = adapt_execution_groups(
            execution_groups=(("planner", "reviewer"),),
            options=options,
            group_history={"planner|reviewer": {"samples": 4, "useful_evidence": 0.8}},
            contribution_history={
                "planner": {"evidence_quality": 0.8},
                "reviewer": {"evidence_quality": 0.8},
                "researcher": {"evidence_quality": 0.9},
            },
        )
        self.assertEqual(groups, (("planner", "researcher", "reviewer"),))
        self.assertEqual(changes[0].action, "add")


    def test_observed_candidate_must_beat_parent_group(self):
        class O:
            def __init__(self, name, phase):
                self.name, self.phase, self.instructions, self.estimated_cost = name, phase, "x", 0.2

        options = (O("planner", "planning"), O("reviewer", "review"), O("researcher", "research"), O("explorer", "discovery"))
        groups, changes = adapt_execution_groups(
            execution_groups=(("planner", "reviewer"),),
            options=options,
            group_history={
                "planner|reviewer": {"samples": 4, "useful_evidence": 0.2, "success_rate": 0.8},
                "researcher|reviewer": {"samples": 3, "useful_evidence": 0.23, "success_rate": 0.9},
            },
            contribution_history={
                "planner": {"evidence_quality": 0.2},
                "reviewer": {"evidence_quality": 0.2},
                "researcher": {"evidence_quality": 0.9},
                "explorer": {"evidence_quality": 0.7},
            },
        )
        self.assertEqual(groups, (("explorer", "reviewer"),))
        self.assertEqual(changes[0].after, ("explorer", "reviewer"))


if __name__ == "__main__":
    unittest.main()

    def test_repeated_failed_candidate_is_not_reselected(self):
        class O:
            def __init__(self, name, phase):
                self.name, self.phase, self.instructions, self.estimated_cost = name, phase, "x", 0.2

        options = (O("planner", "planning"), O("reviewer", "review"), O("researcher", "research"), O("explorer", "discovery"))
        groups, changes = adapt_execution_groups(
            execution_groups=(("planner", "reviewer"),),
            options=options,
            group_history={
                "planner|reviewer": {"samples": 4, "useful_evidence": 0.2},
                "researcher|reviewer": {"samples": 3, "useful_evidence": 0.1, "success_rate": 0.0},
            },
            contribution_history={
                "planner": {"evidence_quality": 0.2},
                "reviewer": {"evidence_quality": 0.25},
                "researcher": {"evidence_quality": 0.9},
                "explorer": {"evidence_quality": 0.8},
            },
        )
        self.assertEqual(groups, (("explorer", "reviewer"),))
        self.assertEqual(changes[0].action, "replace")

