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


def _receipt(request, accepted=True, reason="accepted", evidence_ids=None):
    return BenchmarkExecutionReceipt(
        request.task_id, request.domain, request.holdout,
        tuple(evidence_ids) if evidence_ids is not None else (f"evidence:{request.task_id}",),
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

    def test_attribution_prefers_runtime_resource_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = AutonomousCampaignController(Path(tmp))
            request = _request("resource", "coding")
            prediction = CampaignPrediction("resource", True, .9)
            attribution = controller.attribute(
                request,
                prediction,
                _receipt(request, False, "execution failed"),
                realized_score=.2,
                runtime_signals={"resource_pressure": .9},
            )
            self.assertEqual(attribution.failure_class, "resource")
            self.assertEqual(attribution.runtime_signals["resource_pressure"], .9)

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
            self.assertEqual(result.rollout.state, "promotion_blocked")
            self.assertTrue(any("decision record is required" in reason for reason in result.rollout.reasons))


    def test_holdout_without_evidence_rolls_back_even_when_score_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = AutonomousCampaignController(root, max_tasks=3, lifecycle_canaries=3)
            requests = tuple(_request(x, d) for x, d in (
                ("a", "coding"), ("b", "research"), ("c", "analysis")
            ))
            predictions = {r.task_id: CampaignPrediction(r.task_id, True, .8) for r in requests}
            result = controller.run(
                "campaign-missing-evidence",
                requests,
                execute=lambda request: (_receipt(request, evidence_ids=()), .95),
                predictions=predictions,
                capability_id="candidate-capability",
                baseline_score=.8,
            )
            self.assertEqual(result.rollout.state, "rolled_back")
            self.assertTrue(any("safety gate failed" in reason for reason in result.rollout.reasons))

    def test_trusted_lifecycle_factory_can_promote_only_with_actual_holdout_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted_ids = {
                "run-123", "approval-1", "rollback-test-1",
                "evidence:a", "evidence:b", "evidence:c",
            }
            record = {
                "schema_version": "1.0",
                "task_id": "promotion-task",
                "repository": "owner/project",
                "base_commit": "a" * 40,
                "head_commit": "b" * 40,
                "risk_level": "high",
                "acceptance_criteria": ["Independent holdout canaries pass"],
                "selected_option": "Promote behind a reversible gate",
                "selection_rationale": "Measured score passes the holdout threshold",
                "alternatives": [{"option": "Immediate rollout", "reason_rejected": "Not reversible"}],
                "dependencies": [],
                "checks": [{
                    "name": "canary-regression",
                    "command": "python -m unittest tests.test_autonomous_campaign_learning",
                    "status": "passed",
                    "commit_sha": "b" * 40,
                    "evidence_id": "run-123",
                    "evaluator_type": "internal",
                }],
                "limitations": ["No independent generalization campaign is claimed"],
                "rollback_trigger": "Any required check fails or safety regression is observed",
                "rollback_action": "Restore the last known-good capability and rerun holdouts",
                "rollback_procedure_ref": "runbook://release-rollback",
                "rollback_test_evidence_id": "rollback-test-1",
                "last_known_good_commit": "a" * 40,
                "retry_history": [],
                "promotion": {
                    "level": "verified_for_scope",
                    "approved_by": "release-manager",
                    "approval_evidence_id": "approval-1",
                },
            }

            def lifecycle_factory(path, project, min_canaries):
                from portable.capability_lifecycle import CapabilityLifecycle
                from portable.persistent_memory import PersistentMemory
                return CapabilityLifecycle(
                    PersistentMemory(path / "trusted-lifecycle.sqlite3", require_approval=False),
                    project,
                    min_canaries=min_canaries,
                    promotion_record=record,
                    expected_repository="owner/project",
                    expected_head="b" * 40,
                    authorized_approvers={"release-manager"},
                    known_evidence_ids=trusted_ids,
                )

            controller = AutonomousCampaignController(
                root,
                max_tasks=3,
                lifecycle_canaries=3,
                capability_lifecycle_factory=lifecycle_factory,
            )
            requests = tuple(_request(x, d) for x, d in (
                ("a", "coding"), ("b", "research"), ("c", "analysis")
            ))
            predictions = {r.task_id: CampaignPrediction(r.task_id, True, .8) for r in requests}
            result = controller.run(
                "campaign-trusted-promotion",
                requests,
                execute=lambda request: (_receipt(request), .95),
                predictions=predictions,
                capability_id="candidate-capability",
                baseline_score=.8,
            )
            self.assertEqual(result.rollout.state, "promoted", result.rollout.reasons)
            self.assertEqual(result.rollout.canary_count, 3)


    def test_holdout_cohort_below_promotion_threshold_cannot_promote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = AutonomousCampaignController(
                root,
                max_tasks=3,
                lifecycle_canaries=3,
                minimum_holdout_score=.75,
                minimum_promotion_score=.80,
            )
            requests = tuple(_request(x, d) for x, d in (
                ("a", "coding"), ("b", "research"), ("c", "analysis")
            ))
            predictions = {r.task_id: CampaignPrediction(r.task_id, True, .8) for r in requests}
            result = controller.run(
                "campaign-below-promotion-threshold",
                requests,
                execute=lambda request: (_receipt(request), .77),
                predictions=predictions,
                capability_id="candidate-capability",
                baseline_score=.70,
            )
            self.assertEqual(result.rollout.state, "rolled_back")
            self.assertTrue(any("safety gate failed" in reason for reason in result.rollout.reasons))

    def test_learned_domain_score_calibrates_from_persisted_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = AutonomousCampaignController(root, max_tasks=3)
            request = _request("benchmark:research:observed", "research")
            prediction = CampaignPrediction(request.task_id, True, .9)
            controller.run(
                "calibration-campaign",
                (request,),
                execute=lambda r: (_receipt(r), .4),
                predictions={request.task_id: prediction},
            )
            score = controller.learned_domain_score(root, "research", fallback=.75)
            self.assertLess(score, .75)
            self.assertGreaterEqual(score, 0.0)

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
