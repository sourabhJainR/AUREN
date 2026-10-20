import unittest

from portable.decision_fabric import ChoiceDecision, DecisionQuestion, ScoreDecision
from portable.adaptive_decision import AdaptiveInferencePolicy
from portable.decision_lifecycle import (
    CalibrationObservation,
    DecisionRouter,
    CascadeRequest,
    ConfidenceCalibrator,
    CostAwareCascade,
    ModelCandidate,
    ShadowEvaluator,
    evaluate_batch,
)


class DecisionLifecycleTests(unittest.TestCase):
    @staticmethod
    def _well_sampled_calibrator():
        calibrator = ConfidenceCalibrator(min_samples=10)
        calibrator.extend([CalibrationObservation(0.9, True) for _ in range(20)])
        return calibrator

    def test_calibrator_is_conservative_before_minimum_samples(self):
        calibrator = ConfidenceCalibrator(min_samples=10)
        self.assertLess(calibrator.calibrate(0.9), 0.9)
        self.assertEqual(calibrator.calibrate(0.2), 0.2)
        calibrator.extend([CalibrationObservation(0.9, False) for _ in range(20)])
        self.assertLess(calibrator.calibrate(0.9), 0.9)
        self.assertEqual(calibrator.report().sample_count, 20)

    def test_cascade_respects_privacy_quality_cost_and_latency(self):
        candidates = [
            ModelCandidate("rules", "deterministic", 0, 5, 0.99),
            ModelCandidate("local", "local", 0.02, 100, 0.92),
            ModelCandidate("cloud", "external", 0.2, 400, 0.98),
            ModelCandidate("human", "human", 0, 5000, 1.0),
        ]
        cascade = CostAwareCascade(candidates)
        private = cascade.choose(CascadeRequest(0.8, 0.2, True, 200, 0.1))
        self.assertEqual(private.candidate.lane, "local")
        guarded = cascade.choose(CascadeRequest(0.8, 0.95, False, 1000, 10, irreversible_action=True))
        self.assertTrue(guarded.requires_human_approval)
        self.assertEqual(guarded.candidate.lane, "human")

    def test_batch_validates_all_typed_answers(self):
        question = DecisionQuestion("route", "choice", options=("local", "external"))
        class Provider:
            def evaluate_batch(self, state, questions):
                return {"route": ChoiceDecision("local", {"local": 0.9, "external": 0.1}, 0.9)}
        result = evaluate_batch({"task": "test"}, [question], Provider())
        self.assertIn("route", result.decisions)
        class Missing:
            def evaluate_batch(self, state, questions):
                return {}
        with self.assertRaises(ValueError):
            evaluate_batch({"task": "test"}, [question], Missing())

    def test_router_composes_calibration_cascade_and_shadow_without_execution(self):
        candidates = [
            ModelCandidate("rules", "deterministic", 0, 5, 0.99),
            ModelCandidate("local", "local", 0.02, 100, 0.92),
            ModelCandidate("human", "human", 0, 5000, 1.0),
        ]
        shadow = ShadowEvaluator(policy_version="router-v1")
        router = DecisionRouter(
            inference_policy=AdaptiveInferencePolicy(),
            calibrator=self._well_sampled_calibrator(),
            cascade=CostAwareCascade(candidates),
            shadow=shadow,
        )
        result = router.route(
            {"task_id": "example"},
            uncertainty=0.2, risk=0.2, evidence_quality=0.9,
            complexity=0.8, privacy_required=True,
            latency_budget_ms=200, cost_budget=0.1,
        )
        self.assertEqual(result.selected_model.lane, "local")
        self.assertIsNotNone(result.shadow_record)
        self.assertEqual(len(shadow.records), 1)
        self.assertIsNone(shadow.records[0].outcome_correct)
        observed = shadow.observe_outcome(
            state_digest_value=result.shadow_record.state_digest, outcome_correct=True
        )
        self.assertTrue(observed.outcome_correct)
        self.assertTrue(shadow.promotion_eligible(minimum_samples=1, minimum_accuracy=1.0))

    def test_shadow_only_keeps_baseline_route_while_recording_candidate(self):
        shadow = ShadowEvaluator(policy_version="shadow-only-v1")
        router = DecisionRouter(
            inference_policy=AdaptiveInferencePolicy(),
            calibrator=self._well_sampled_calibrator(),
            cascade=CostAwareCascade([
                ModelCandidate("rules", "deterministic", 0, 5, 0.99),
                ModelCandidate("local", "local", 0.02, 100, 0.92),
                ModelCandidate("human", "human", 0, 5000, 1.0),
            ]),
            shadow=shadow,
            shadow_only=True,
        )
        result = router.route(
            {"task_id": "shadow"},
            uncertainty=0.2, risk=0.2, evidence_quality=0.9,
            complexity=0.8, privacy_required=True,
            latency_budget_ms=200, cost_budget=0.1,
        )
        self.assertEqual(result.selected_model.lane, "deterministic")
        self.assertEqual(result.candidate_model.lane, "local")
        self.assertIn("shadow-only", result.reason)
        self.assertEqual(result.shadow_record.baseline, "deterministic")
        self.assertEqual(result.shadow_record.candidate, "local")

    def test_router_escalates_irreversible_action_to_human(self):
        router = DecisionRouter(
            inference_policy=AdaptiveInferencePolicy(),
            calibrator=ConfidenceCalibrator(min_samples=10),
            cascade=CostAwareCascade([
                ModelCandidate("rules", "deterministic", 0, 5, 0.99),
                ModelCandidate("human", "human", 0, 5000, 1.0),
            ]),
        )
        result = router.route(
            {"task_id": "irreversible"},
            uncertainty=0.1, risk=0.1, evidence_quality=0.99,
            complexity=0.1, privacy_required=True,
            latency_budget_ms=10000, cost_budget=10,
            irreversible_action=True,
        )
        self.assertEqual(result.selected_model.lane, "human")
        self.assertTrue(result.requires_human_approval)

    def test_calibration_and_shadow_emit_provenance_events_to_sink(self):
        class Sink:
            def __init__(self):
                self.events = []
            def append(self, event):
                self.events.append(event)

        sink = Sink()
        calibrator = ConfidenceCalibrator(min_samples=3, evidence_sink=sink)
        calibrator.add(CalibrationObservation(0.8, True))
        shadow = ShadowEvaluator(policy_version="shadow-v2", evidence_sink=sink)
        shadow.compare({"task": "unit"}, baseline="local", candidate="external")
        self.assertEqual(len(sink.events), 2)
        self.assertNotEqual(sink.events[0].event_id, sink.events[1].event_id)
        self.assertTrue(all(event.state_digest for event in sink.events))
        self.assertEqual(sink.events[1].policy_version, "shadow-v2")

    def test_shadow_does_not_promote_without_enough_evidence(self):
        shadow = ShadowEvaluator(policy_version="route-v1")
        shadow.compare({"id": 1}, baseline="local", candidate="cloud", outcome_correct=True)
        self.assertFalse(shadow.promotion_eligible(minimum_samples=2))
        shadow.compare({"id": 2}, baseline="local", candidate="local", outcome_correct=True)
        self.assertTrue(shadow.promotion_eligible(minimum_samples=2, minimum_accuracy=0.95))


if __name__ == "__main__":
    unittest.main()
