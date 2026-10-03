import unittest

from portable.agi_evaluation import KINDS
from portable.general_intelligence_evaluation import ExecutionBackedEvaluator, ExecutionCase


class ExecutionBackedEvaluatorTests(unittest.TestCase):
    def _case(self, index, *, domain, kind, holdout=True, ok=True, evidence=True):
        return ExecutionCase(
            case_id=f"case-{index}",
            kind=kind,
            domain=domain,
            holdout=holdout,
            runner=lambda: {"value": index, "ok": ok},
            oracle=lambda observed: observed["ok"],
            evidence_factory=lambda observed: (f"evidence-{index}",) if evidence else (),
        )

    def test_runs_real_cases_and_requires_breadth_and_holdouts(self):
        kinds = tuple(KINDS)[:6]
        cases = [
            self._case(i, domain=f"d{i % 4}", kind=kinds[i % len(kinds)], holdout=i % 2 == 0)
            for i in range(8)
        ]
        report = ExecutionBackedEvaluator().evaluate(cases)
        self.assertEqual(report.total, 8)
        self.assertEqual(report.passed, 8)
        self.assertEqual(report.verified, 8)
        self.assertTrue(report.breadth_passed)
        self.assertTrue(report.gate_passed)

    def test_missing_evidence_fails_closed(self):
        kinds = tuple(KINDS)[:6]
        cases = [
            self._case(i, domain=f"d{i % 4}", kind=kinds[i % len(kinds)], evidence=i != 0)
            for i in range(8)
        ]
        report = ExecutionBackedEvaluator().evaluate(cases)
        self.assertFalse(report.gate_passed)
        self.assertFalse(report.results[0].verified)

    def test_duplicate_evidence_is_not_independent(self):
        kinds = tuple(KINDS)[:6]
        cases = [
            ExecutionCase(
                case_id=f"case-{i}",
                kind=kinds[i % len(kinds)],
                domain=f"d{i % 4}",
                holdout=True,
                runner=lambda: True,
                oracle=lambda value: value,
                evidence_factory=lambda value: ("shared-evidence",),
            )
            for i in range(8)
        ]
        report = ExecutionBackedEvaluator().evaluate(cases)
        self.assertFalse(report.breadth_passed)
        self.assertFalse(report.gate_passed)

    def test_oracle_failure_is_recorded(self):
        case = self._case(1, domain="reasoning", kind="reasoning", ok=False)
        report = ExecutionBackedEvaluator(
            minimum_domains=2, minimum_holdout_domains=1, minimum_kinds=1
        ).evaluate([case])
        self.assertEqual(report.passed, 0)
        self.assertFalse(report.results[0].verified)
        self.assertIn("oracle rejected", report.results[0].error)


if __name__ == "__main__":
    unittest.main()
