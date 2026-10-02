import unittest

from portable.controlled_experiment import (
    ControlledExperimentAttributor,
    ExperimentObservation,
)


class ControlledExperimentTests(unittest.TestCase):
    def obs(self, oid, cohort, metric, *evidence, holdout=True):
        return ExperimentObservation("exp-1", oid, cohort, metric, tuple(evidence), holdout)

    def test_requires_independent_control_and_treatment(self):
        rows = [
            self.obs("c1", "control", .60, "c1"),
            self.obs("c2", "control", .62, "c2"),
            self.obs("t1", "treatment", .66, "t1"),
            self.obs("t2", "treatment", .68, "t2"),
        ]
        result = ControlledExperimentAttributor().evaluate("exp-1", rows)
        self.assertTrue(result.reproducible)
        self.assertAlmostEqual(result.lift, .06)

    def test_single_cohort_cannot_claim_causality(self):
        rows = [self.obs("t1", "treatment", .90, "t1"), self.obs("t2", "treatment", .91, "t2")]
        result = ControlledExperimentAttributor().evaluate("exp-1", rows)
        self.assertFalse(result.reproducible)

    def test_overlapping_evidence_is_rejected(self):
        rows = [
            self.obs("c1", "control", .60, "same"),
            self.obs("t1", "treatment", .70, "same"),
        ]
        result = ControlledExperimentAttributor().evaluate("exp-1", rows)
        self.assertFalse(result.reproducible)
        self.assertIn("overlap", result.reason)

    def test_non_holdout_observations_do_not_count(self):
        rows = [self.obs("c1", "control", .60, "c1", holdout=False)]
        result = ControlledExperimentAttributor().evaluate("exp-1", rows)
        self.assertFalse(result.reproducible)


if __name__ == "__main__":
    unittest.main()
