import unittest

from portable.agency_quality import ReviewFinding, evaluate, score_dimensions, evaluate_versioned_rubric
from portable.evaluation_rubric import RubricCriterion, RubricDefinition


RUBRIC = {
    "release_threshold": 90,
    "dimensions": {
        "correctness": 25,
        "completeness": 15,
        "evidence": 15,
        "verification": 15,
        "scope_discipline": 10,
        "security_and_safety": 10,
        "clarity": 5,
        "maintainability": 5,
    },
}


class AgencyQualityTests(unittest.TestCase):
    def test_score_dimensions_caps_values(self):
        self.assertEqual(score_dimensions({"correctness": 25, "completeness": 15}, RUBRIC), 40)

    def test_score_dimensions_rejects_unknown_dimension(self):
        with self.assertRaises(ValueError):
            score_dimensions({"not_in_rubric": 1}, RUBRIC)

    def test_receipt_requires_score_gates_and_no_material_findings(self):
        receipt = evaluate(
            {name: cap for name, cap in RUBRIC["dimensions"].items()},
            {"acceptance": True, "verification": True},
            rubric=RUBRIC,
        )
        self.assertTrue(receipt.passed)

        blocked = evaluate(
            {name: cap for name, cap in RUBRIC["dimensions"].items()},
            {"acceptance": True, "verification": True},
            [ReviewFinding("material", "API behavior is not backward compatible")],
            rubric=RUBRIC,
        )
        self.assertFalse(blocked.passed)


    def test_versioned_rubric_uses_canonical_agency_quality_entry_point(self):
        rubric = RubricDefinition(
            "agency-delivery", "1.0.0",
            (
                RubricCriterion("correctness", "Correctness", 50, ("tests",), True),
                RubricCriterion("evidence", "Evidence", 50, ("report",)),
            ),
            hard_gate_ids=("guard",),
        )
        receipt = evaluate_versioned_rubric(
            rubric, subject_id="candidate", run_id="run-1",
            source_digest="a" * 64, plan_digest="b" * 64,
            artifact_digests=("c" * 64,), evaluator_id="qa", evaluator_version="qa-v1",
            executor_id="engineer", scores={"correctness": 100, "evidence": 100},
            evidence={"correctness": ["tests://1"], "evidence": ["report://1"]},
            rationales={"correctness": "Tests passed", "evidence": "Report attached"},
            hard_gates={"guard": True},
        )
        self.assertEqual(receipt.status, "passed")
        self.assertEqual(receipt.rubric_digest, rubric.digest)

    def test_finding_severity_is_validated(self):
        with self.assertRaises(ValueError):
            ReviewFinding("critical", "bad")

    def test_default_rubric_supports_standard_dimensions(self):
        receipt = evaluate({name: cap for name, cap in RUBRIC["dimensions"].items()}, {"verification": True})
        self.assertEqual(receipt.score, 100)
        self.assertEqual(receipt.threshold, 90)


if __name__ == "__main__":
    unittest.main()
