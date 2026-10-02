import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.capability_graduation import CapabilityGraduationController
from portable.cross_task_capability_abstraction import CapabilityPattern, TransferValidation
from portable.failure_cluster_invention import CapabilityInvention
from portable.invention_lifecycle import EvidenceBackedInventionLifecycle


def _invention():
    return CapabilityInvention(
        "inv1", "repeated timeout", ("verify", "parallel"),
        "reduce timeout failures", 4, .3, .8
    )


def _pattern():
    return CapabilityPattern(
        "p1", "evidence-first", "parallel", "c|d|r|l|e|f",
        ("verified-outcome",), "improve evidence", 6, .9, .9
    )


def _validation(eid, uplift=.1, promoted=True, regression=True):
    return TransferValidation(
        "p1", 2, .9, .8, uplift, regression, True, promoted, (eid,), "ok"
    )


class InventionLifecycleTests(unittest.TestCase):
    def test_holdouts_are_disjoint_from_trigger_evidence(self):
        with TemporaryDirectory() as td:
            lifecycle = EvidenceBackedInventionLifecycle(Path(td))
            with self.assertRaises(ValueError):
                lifecycle.build_holdout_request(
                    _invention(), _pattern(),
                    trigger_evidence=("trigger-a", "trigger-b"),
                    holdout_ids=("trigger-b", "holdout-c"),
                )

    def test_positive_transfer_enters_graduation(self):
        with TemporaryDirectory() as td:
            lifecycle = EvidenceBackedInventionLifecycle(Path(td))
            result = lifecycle.evaluate(
                _invention(), _pattern(),
                [_validation("holdout-a"), _validation("holdout-b")],
                trigger_evidence=("trigger-a",),
                holdout_ids=("holdout-a", "holdout-b"),
                graduation=CapabilityGraduationController(minimum_cohorts=2),
            )
            self.assertEqual(result.state, "promoted")
            self.assertEqual(result.rollout.passing_cohorts, 2)
            self.assertGreater(result.benchmark_delta, 0)

    def test_regression_retires_invention_path(self):
        with TemporaryDirectory() as td:
            lifecycle = EvidenceBackedInventionLifecycle(Path(td))
            result = lifecycle.evaluate(
                _invention(), _pattern(),
                [
                    _validation("holdout-a", -.1, False, False),
                    _validation("holdout-b", -.2, False, False),
                ],
                trigger_evidence=("trigger-a",),
                holdout_ids=("holdout-a", "holdout-b"),
                graduation=CapabilityGraduationController(
                    minimum_cohorts=2, retirement_regressions=2
                ),
            )
            self.assertEqual(result.state, "retired")


if __name__ == "__main__":
    unittest.main()
