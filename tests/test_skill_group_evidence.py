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
                self.name, self.phase, self.instructions = name, phase, "x"

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
        self.assertEqual(groups, (("planner", "researcher"),))
        self.assertEqual(changes[0].action, "replace")

    def test_strong_group_can_add_complementary_skill(self):
        class O:
            def __init__(self, name, phase):
                self.name, self.phase, self.instructions = name, phase, "x"

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


if __name__ == "__main__":
    unittest.main()
