import tempfile
import unittest
from pathlib import Path

from portable.autonomous_campaign_learning import (
    AutonomousCampaignController,
    CampaignPrediction,
)
from portable.benchmark_execution_handshake import BenchmarkExecutionReceipt
from portable.benchmark_task_dispatch import BenchmarkExecutionRequest


def _request(task_id, domain, holdout=True):
    return BenchmarkExecutionRequest(
        task_id=task_id, domain=domain, holdout=holdout,
        objective=f"test {domain}", risk_budget=.2, resource_budget=.5,
        required_evidence=("canonical execution evidence",),
    )


def _receipt(request, accepted=True, reason="accepted"):
    return BenchmarkExecutionReceipt(
        request.task_id, request.domain, request.holdout,
        (f"evidence:{request.task_id}",),
        ("canonical execution evidence",),
        request.required_evidence,
        accepted, True, reason,
    )


class AutonomousCampaignLearningTests(unittest.TestCase):
    def test_selection_prefers_holdouts_and_is_bounded(self):
        controller = AutonomousCampaignController(Path("."), max_tasks=3)
        rows = (
            _request("a", "coding", False),
            _request("b", "research", True),
            _request("c", "analysis", True),
            _request("d", "planning", True),
        )
        selected = controller.select(rows)
        self.assertEqual([x.task_id for x in selected], ["c", "d", "b"])

    def test_attribution_detects_verification_failure(self):
        request = _request("a", "coding")
        prediction = CampaignPrediction("a", True, .9)
        attribution = AutonomousCampaignController.attribute(
            request, prediction, _receipt(request, False, "independent verification required"),
            realized_score=.2,
        )
        self.assertEqual(attribution.failure_class, "verification")
        self.assertGreater(attribution.score_error, .6)

    def test_closed_loop_persists_learning_and_promotes_after_holdout_canaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = AutonomousCampaignController(root, max_tasks=3, lifecycle_canaries=3)
            requests = tuple(_request(x, d) for x, d in (
                ("a", "coding"), ("b", "research"), ("c", "analysis")
            ))
            predictions = {r.task_id: CampaignPrediction(r.task_id, True, .8) for r in requests}

            def execute(request):
                return _receipt(request), .9

            result = controller.run(
                "campaign-1", requests, execute=execute, predictions=predictions,
                capability_id="candidate-capability", baseline_score=.8,
            )
            self.assertEqual(result.selected_task_ids, ("c", "a", "b"))
            self.assertEqual(result.holdout_task_ids, ("c", "a", "b"))
            self.assertEqual(len(result.observations), 3)
            self.assertEqual(result.rollout.state, "promoted")

    def test_regressed_holdout_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = AutonomousCampaignController(root, max_tasks=3, lifecycle_canaries=3)
            requests = tuple(_request(x, d) for x, d in (
                ("a", "coding"), ("b", "research"), ("c", "analysis")
            ))
            predictions = {r.task_id: CampaignPrediction(r.task_id, True, .9) for r in requests}
            result = controller.run(
                "campaign-rollback", requests,
                execute=lambda request: (_receipt(request), .6),
                predictions=predictions,
                capability_id="candidate-capability", baseline_score=.8,
            )
            self.assertEqual(result.rollout.state, "rolled_back")


if __name__ == "__main__":
    unittest.main()
