import tempfile
import unittest
from pathlib import Path

from portable.learning_steward import LearningSteward


class CapabilityOutcomeCalibrationTests(unittest.TestCase):
    def test_capability_outcome_is_retrievable_by_exact_capability_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            steward = LearningSteward(root, run_id="run-1", task="inspect repository")
            steward.record_experience(
                key="explorer:inspect repository:capability:skill:repo-review",
                outcome="passed",
                evidence_quality=0.95,
                cost_score=0.2,
                duration_seconds=1.5,
                decision="selected_capability=skill:repo-review",
                evidence_ids=["agent:explorer"],
            )
            rows = LearningSteward.experience_history(
                root,
                "explorer:inspect repository:capability:skill:repo-review",
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "worked")
            self.assertIn("selected_capability=skill:repo-review", rows[0]["detail"])

    def test_capability_history_does_not_replace_role_task_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            steward = LearningSteward(root, run_id="run-2", task="inspect repository")
            steward.record_experience(
                key="explorer:inspect repository",
                outcome="passed",
                evidence_quality=0.9,
                cost_score=0.3,
                duration_seconds=2.0,
                decision="capability=core",
                evidence_ids=["agent:explorer"],
            )
            steward.record_experience(
                key="explorer:inspect repository:capability:core",
                outcome="failed",
                evidence_quality=0.1,
                cost_score=0.4,
                duration_seconds=3.0,
                decision="selected_capability=core",
                evidence_ids=["agent:explorer"],
            )
            aggregate = LearningSteward.experience_history(root, "explorer:inspect repository")
            exact = LearningSteward.experience_history(root, "explorer:inspect repository:capability:core")
            self.assertEqual(len(aggregate), 2)
            self.assertEqual(len(exact), 1)
            self.assertEqual(exact[0]["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
