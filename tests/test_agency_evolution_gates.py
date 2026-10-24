"""Tests for AUREN's evidence-driven evolution gates."""
import json
import tempfile
import unittest
from pathlib import Path

from portable.agency_evolution_gates import (
    ExperimentManifest,
    ImprovementHypothesis,
    ImprovementJournal,
    Signal,
    detect_drift,
    evaluate_promotion_gates,
    evaluate_evaluator_calibration,
    extract_signals_from_trace,
    prioritize_signals,
)


class AgencyEvolutionGatesTests(unittest.TestCase):
    def test_signal_priority_values_security_and_impact_over_frequency(self):
        frequent = Signal("freq", "latency", "slow requests", "trace:1", recurrence=1.0,
                          severity=0.2, expected_impact=0.2, security_relevance=0.0)
        security = Signal("security", "boundary", "missing authorization check", "trace:2",
                          recurrence=0.1, severity=0.9, expected_impact=0.8,
                          security_relevance=1.0)
        self.assertEqual(prioritize_signals([frequent, security])[0].signal_id, "security")

    def test_hypothesis_requires_evidence_and_valid_state_transitions(self):
        hypothesis = ImprovementHypothesis(
            hypothesis_id="hyp-1", problem="repeated verification failure",
            evidence_refs=("trace:123",), proposed_change="add an invariant check",
            expected_benefit="fewer invalid releases", acceptance_criteria=("regression passes",),
            risk="low", budget_units=3, baseline_revision="base-sha",
            candidate_revision="candidate-sha", rollback_plan="revert candidate",
        )
        investigated = hypothesis.transition("investigated")
        proposed = investigated.transition("hypothesized")
        planned = proposed.transition("planned")
        evaluated = planned.transition("evaluated")
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            evaluated.transition("approved")
        approved = evaluated.transition("approved", reviewer_id="independent-reviewer", decision_ref="review:7")
        self.assertEqual(approved.state, "approved")
        with self.assertRaisesRegex(ValueError, "invalid hypothesis transition"):
            approved.transition("verified", reviewer_id="reviewer", decision_ref="decision")

    def test_experiment_manifest_rejects_sealed_holdout_payload(self):
        with self.assertRaisesRegex(ValueError, "must never be embedded"):
            ExperimentManifest(
                experiment_id="exp-1", hypothesis_id="hyp-1", baseline_revision="base",
                candidate_revision="candidate", dataset_digest="dataset-sha",
                evaluator_revision="judge-v2", seed=7, budget_units=10,
                metrics=("quality",), holdout_reference="sealed-set-v1",
                holdout_payload_included=True,
            )

    def test_fixed_baseline_detects_degradation_even_when_adaptive_baseline_moves(self):
        result = detect_drift(
            "quality", [0.78, 0.79, 0.80], fixed_baseline=0.9,
            adaptive_baseline=0.79, absolute_tolerance=0.05, adaptive_tolerance=0.05,
        )
        self.assertTrue(result.drift_detected)
        self.assertEqual(result.reason, "fixed-release-baseline")
        self.assertLess(result.absolute_delta, 0)

    def test_promotion_fails_closed_for_missing_gates_and_never_promotes_directly(self):
        decision = evaluate_promotion_gates(
            {"evaluator_independent": True, "holdout_isolated": True},
            candidate_passed=True, regression_passed=True, risk_approved=True,
        )
        self.assertEqual(decision.action, "block")
        self.assertIn("evidence_integrity", decision.missing_gates)
        ready = evaluate_promotion_gates(
            {
                "evaluator_independent": True, "holdout_isolated": True,
                "evidence_integrity": True, "required_checks_passed": True,
                "rollback_proven": True, "approval_recorded": True,
            },
            candidate_passed=True, regression_passed=True, risk_approved=True,
        )
        self.assertEqual(ready.action, "eligible_for_controller_approval")

    def test_journal_is_hash_chained_idempotent_and_conflict_detecting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "improvement.jsonl"
            journal = ImprovementJournal(path)
            first = journal.append(event_id="evt-1", work_id="work-1", state="observed",
                                   payload={"source": "trace:1", "count": 2})
            duplicate = journal.append(event_id="evt-1", work_id="work-1", state="observed",
                                       payload={"count": 2, "source": "trace:1"})
            self.assertEqual(duplicate, first)
            second = journal.append(event_id="evt-2", work_id="work-1", state="investigated",
                                    payload={"review": "independent"})
            self.assertEqual([event.sequence for event in journal.read()], [1, 2])
            self.assertEqual(second.previous_digest, first.digest)
            with self.assertRaisesRegex(ValueError, "conflicting content"):
                journal.append(event_id="evt-1", work_id="work-1", state="deployed", payload={})

    def test_journal_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "improvement.jsonl"
            journal = ImprovementJournal(path)
            journal.append(event_id="evt-1", work_id="work-1", state="observed", payload={"score": 0.5})
            row = json.loads(path.read_text(encoding="utf-8"))
            row["payload"]["score"] = 0.99
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                journal.read()


    def test_trace_outcomes_become_safe_source_linked_signals(self):
        trace = {
            "trace_id": "trace-42", "start": 100.0, "status": "failed",
            "duration_ms": 40000,
            "spans": [{"span_id": "verify-1", "status": "failed", "output": "do not copy this secret"}],
            "scores": [{"name": "correctness", "value": 0.4, "reason": "private detail"}],
            "metadata": {"regressions": [{"regression_id": "reg-1", "passed": False}]},
        }
        signals = extract_signals_from_trace(trace, low_score_threshold=0.6, slow_trace_ms=30000)
        self.assertEqual(len(signals), 4)
        self.assertTrue(all(signal.source_ref == "trace:trace-42" for signal in signals))
        self.assertTrue(all("secret" not in signal.summary and "private detail" not in signal.summary for signal in signals))
        self.assertEqual(len({signal.signal_id for signal in signals}), len(signals))

    def test_evaluator_calibration_blocks_insufficient_samples_and_false_passes(self):
        small = evaluate_evaluator_calibration("judge-v1", [(True, True)] * 10, minimum_samples=20)
        self.assertFalse(small.accepted)
        self.assertEqual(small.reason, "insufficient-independent-cases")
        cases = [(True, True)] * 25 + [(False, False)] * 24 + [(False, True)]
        report = evaluate_evaluator_calibration(
            "judge-v2", cases, minimum_samples=40, minimum_accuracy=0.95,
            maximum_false_pass_rate=0.02,
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.false_pass_rate, 1 / 25)
        self.assertEqual(report.reason, "false-pass-rate-above-threshold")


if __name__ == "__main__":
    unittest.main()
