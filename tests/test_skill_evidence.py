import unittest

from portable.skill_evidence import attribute, assess_collaboration


class SkillEvidenceTests(unittest.TestCase):
    def test_reviewer_gets_conservative_credit_when_findings_are_observed(self):
        rows = attribute(
            members=(
                {"name": "planner", "source": "skill", "phase": "planning"},
                {"name": "reviewer", "source": "skill", "phase": "review"},
            ),
            execution_groups=(("planner",), ("reviewer",)),
            output="## Findings\n- reviewer found a regression\n## Evidence\n- test reproduced the issue",
            status="passed",
            evidence_quality=0.9,
            role="correctness reviewer",
        )
        by_name = {row.skill: row for row in rows}
        self.assertGreater(by_name["reviewer"].contribution, by_name["planner"].contribution)
        self.assertGreater(by_name["reviewer"].unique_findings, 0)

    def test_explicit_skill_section_is_attributed_to_that_skill_only(self):
        rows = attribute(
            members=(
                {"name": "planner", "source": "skill", "phase": "planning"},
                {"name": "reviewer", "source": "skill", "phase": "review"},
            ),
            execution_groups=(("planner",), ("reviewer",)),
            output="""## Skill Evidence: planner
- produced a plan
## Skill Evidence: reviewer
- found a regression
- test reproduced the issue
""",
            status="passed",
            evidence_quality=0.8,
            role="correctness reviewer",
        )
        by_name = {row.skill: row for row in rows}
        self.assertEqual(by_name["planner"].unique_findings, 0)
        self.assertEqual(by_name["reviewer"].unique_findings, 1)
        self.assertEqual(by_name["reviewer"].evidence_signals, 1)

    def test_redundant_member_is_not_credited_as_equal_contributor(self):
        rows = attribute(
            members=(
                {"name": "planner", "source": "skill", "phase": "planning"},
                {"name": "planner-copy", "source": "skill", "phase": "planning"},
            ),
            execution_groups=(("planner", "planner-copy"),),
            output="planner produced the verified plan",
            status="passed",
            evidence_quality=0.8,
            role="planner",
        )
        by_name = {row.skill: row for row in rows}
        self.assertTrue(by_name["planner-copy"].redundant)
        self.assertGreater(by_name["planner"].contribution, by_name["planner-copy"].contribution)

    def test_bundle_without_incremental_evidence_is_not_promotable(self):
        result = assess_collaboration(
            bundle_id="bundle-1",
            bundle_quality=0.80,
            singleton_history={
                "planner": {"samples": 5, "evidence_quality": 0.79, "avg_cost": 0.2},
                "reviewer": {"samples": 5, "evidence_quality": 0.80, "avg_cost": 0.2},
            },
            members=("planner", "reviewer"),
            bundle_cost=0.25,
            bundle_latency_seconds=2.0,
        )
        self.assertFalse(result.promotable)
        self.assertLess(result.collaboration_delta, 0.05)

    def test_positive_delta_with_bounded_cost_is_promotable_signal(self):
        result = assess_collaboration(
            bundle_id="bundle-2",
            bundle_quality=0.92,
            singleton_history={
                "planner": {"samples": 5, "evidence_quality": 0.75, "avg_cost": 0.2},
                "reviewer": {"samples": 5, "evidence_quality": 0.78, "avg_cost": 0.2},
            },
            members=("planner", "reviewer"),
            bundle_cost=0.35,
            bundle_latency_seconds=2.0,
        )
        self.assertTrue(result.promotable)
        self.assertGreaterEqual(result.collaboration_delta, 0.05)

    def test_missing_singleton_baseline_stays_experimental(self):
        result = assess_collaboration(
            bundle_id="bundle-3",
            bundle_quality=1.0,
            singleton_history={},
            members=("planner", "reviewer"),
            bundle_cost=0.1,
            bundle_latency_seconds=1.0,
        )
        self.assertFalse(result.promotable)
        self.assertIsNone(result.singleton_baseline)


if __name__ == "__main__":
    unittest.main()
