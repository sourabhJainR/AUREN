import tempfile
import unittest
from pathlib import Path

from portable.autonomous_campaign_learning import (
    CampaignAttribution, CampaignIntervention, CampaignLearning,
)
from portable.campaign_regression_bridge import CampaignRegressionBridge


class CampaignRegressionBridgeTests(unittest.TestCase):
    def _learning(self, success=False, error=.7):
        row = CampaignAttribution(
            "task:failed", True, success, .9, .2 if not success else .8,
            error, "capability" if not success else "unknown", .9,
            ("evidence:task",), "observed campaign outcome",
        )
        return CampaignLearning(
            "campaign-regression", (row,), (
                CampaignIntervention(
                    "task:failed", row.failure_class,
                    "probe independent holdout", True,
                    "holdout required",
                ),
            ), ("task:failed",), ("task:failed",), None,
        )

    def test_failed_campaign_becomes_candidate_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = CampaignRegressionBridge(Path(tmp)).apply(self._learning())
            self.assertEqual(len(result.candidate_case_ids), 1)
            self.assertEqual(result.validated_case_ids, ())

    def test_material_miscalibration_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            learning = self._learning(success=True, error=.25)
            result = CampaignRegressionBridge(Path(tmp)).apply(learning)
            self.assertEqual(len(result.candidate_case_ids), 1)

    def test_retirement_requires_active_replacement_and_fresh_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = CampaignRegressionBridge(Path(tmp))
            first = bridge.apply(self._learning())
            old_id = first.candidate_case_ids[0]
            second = bridge.apply(self._learning(success=True, error=.25))
            replacement_id = second.candidate_case_ids[0]
            bridge.validate(
                self._learning(),
                case_ids=(replacement_id,),
                evidence_by_case={replacement_id: ("h1",)},
            )
            bridge.validate(
                self._learning(),
                case_ids=(replacement_id,),
                evidence_by_case={replacement_id: ("h2",)},
            )
            retired = bridge.retire_if_validated_replacement(
                old_case_id=old_id,
                replacement_case_id=replacement_id,
                holdout_evidence_ids=("fresh-holdout",),
            )
            self.assertEqual(retired.status, "superseded")

    def test_verified_independent_holdout_can_activate_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = CampaignRegressionBridge(Path(tmp))
            result = bridge.apply(self._learning())
            case_id = result.candidate_case_ids[0]
            validated = bridge.apply(
                self._learning(),
                validation_case_ids=(case_id,),
                evidence_by_case={case_id: ("holdout:independent",)},
            )
            # One validation is intentionally insufficient: corpus requires
            # repeated independent passes before activation.
            self.assertEqual(validated.validated_case_ids, ())
            bridge.apply(
                self._learning(),
                validation_case_ids=(case_id,),
                evidence_by_case={case_id: ("holdout:independent-2",)},
            )
            self.assertEqual(bridge.corpus.get(case_id).status, "active")


if __name__ == "__main__":
    unittest.main()
