"""Tests for AUREN's evidence-driven evolution gates."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portable.agency_evolution_gates import (
    ExperimentManifest,
    ImprovementHypothesis,
    ImprovementJournal,
    GraphLearningEvidenceGate,
    GraphLearningPolicy,
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
        self.assertEqual(len(signals), 5)
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



class GraphLearningEvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = ImprovementJournal(Path(self.temp.name) / "graph-learning.jsonl")
        self.task_ids = ["train-a", "train-b", "holdout-a", "holdout-b"]
        self.training_ids = ["train-a", "train-b"]
        self.holdout_ids = ["holdout-a", "holdout-b"]
        task_set_digest = self.digest(sorted(self.task_ids))
        metrics = {
            task_id: {
                "latency_ms": 100.0, "cost_units": 5.0,
                "quality_score": 0.90, "regression_count": 0,
            }
            for task_id in self.task_ids
        }
        baseline_payload = {
            "baseline_id": "pinned-graph-baseline-v1",
            "task_ids": self.task_ids,
            "training_task_ids": self.training_ids,
            "holdout_task_ids": self.holdout_ids,
            "task_set_digest": task_set_digest,
            "graph_topology": {"parallel_width": 1, "edges": [["train-a", "holdout-a"]]},
            "concurrency": 1,
            "model_provider": {"planner": "model-a", "worker": "provider-a"},
            "worker_selection": ["worker-a"],
            "metrics": metrics,
        }
        self.baseline = {
            **baseline_payload,
            "baseline_digest": self.digest(baseline_payload),
        }
        self.candidate = {
            "baseline_id": self.baseline["baseline_id"],
            "baseline_digest": self.baseline["baseline_digest"],
            "task_ids": list(self.task_ids),
            "training_task_ids": list(self.training_ids),
            "holdout_task_ids": list(self.holdout_ids),
            "task_set_digest": task_set_digest,
            "graph_topology": {"parallel_width": 2, "edges": [["train-a", "holdout-a"], ["train-b", "holdout-b"]]},
            "concurrency": 2,
            "model_provider": {"planner": "model-a", "worker": "provider-b"},
            "worker_selection": ["worker-b", "worker-c"],
            "metrics": {
                task_id: {
                    "latency_ms": 80.0, "cost_units": 4.0,
                    "quality_score": 0.92, "regression_count": 0,
                }
                for task_id in self.task_ids
            },
            "task_digest": self.digest("task-graph"),
            "intent_digest": self.digest("immutable-intent"),
            "artifact_digest": self.digest("candidate-artifact"),
            "source_revision": "a" * 40,
        }
        self.receipt = {
            "schema": "auren-independent-graph-verification-v1",
            "status": "passed",
            "verification_mode": "fresh-isolated-worktree",
            "external_evaluator_independence": "not_proven",
            "worker_identity": "worker-a",
            "verifier_identity": "verifier-ci",
            "task_digest": self.candidate["task_digest"],
            "intent_digest": self.candidate["intent_digest"],
            "source_revision": self.candidate["source_revision"],
            "artifact_digest": self.candidate["artifact_digest"],
            "checks": [{
                "check_id": "unit-tests", "argv": ["python", "-m", "unittest"],
                "status": "passed", "return_code": 0,
            }],
        }
        self.receipt["receipt_digest"] = self.digest(self.receipt)
        self.holdout_evidence = {
            task_id: {
                "baseline_ref": f"baseline://{task_id}",
                "candidate_ref": f"candidate://{task_id}",
                "scored": True,
                "verifier_identity": "verifier-ci",
                "receipt_digest": self.receipt["receipt_digest"],
            }
            for task_id in self.holdout_ids
        }
        self.gate = GraphLearningEvidenceGate(
            self.journal,
            pinned_baseline_id=self.baseline["baseline_id"],
            pinned_baseline_digest=self.baseline["baseline_digest"],
            trusted_verifier_ids=["verifier-ci"],
            policy=GraphLearningPolicy(
                minimum_quality=0.85, maximum_quality_drop=0.02,
                maximum_regressions=0, maximum_cost_increase_fraction=0.10,
                maximum_latency_increase_fraction=0.10, minimum_holdout_tasks=2,
            ),
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def evaluate(self, **overrides):
        args = {
            "run_id": "graph-learning-run-1",
            "baseline": self.baseline,
            "candidate": self.candidate,
            "verification_receipt": self.receipt,
            "holdout_evidence": self.holdout_evidence,
        }
        args.update(overrides)
        return self.gate.evaluate(**args)

    def test_same_task_set_pinned_baseline_and_holdouts_can_only_recommend_review(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "eligible_for_controller_review")
        self.assertTrue(all(result["safety_gates"].values()))
        self.assertEqual(result["baseline_digest"], self.baseline["baseline_digest"])
        self.assertEqual(result["task_set_digest"], self.candidate["task_set_digest"])
        self.assertEqual(result["holdout_task_ids"], self.holdout_ids)
        self.assertEqual(result["recommendation"]["type"], "graph_topology_or_worker_selection")
        self.assertFalse(result["recommendation"]["authority_change"])
        self.assertFalse(result["recommendation"]["policy_change"])
        self.assertFalse(result["recommendation"]["verification_threshold_change"])
        self.assertFalse(result["recommendation"]["rollback_requirement_change"])
        self.assertFalse(result["promotion_eligible"])
        self.assertTrue(result["controller_approval_required"])
        self.assertFalse(result["external_evaluator_independence_proven"])
        self.assertEqual(len(self.journal.read()), 1)

    def test_candidate_is_blocked_on_quality_regression_or_any_safety_regression(self):
        metrics = {
            task_id: {
                "latency_ms": 70.0, "cost_units": 3.0,
                "quality_score": 0.60, "regression_count": 1,
            }
            for task_id in self.task_ids
        }
        candidate = {**self.candidate, "metrics": metrics}
        result = self.evaluate(candidate=candidate)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["recommendation"])
        self.assertFalse(result["safety_gates"]["minimum_quality"])
        self.assertFalse(result["safety_gates"]["zero_regressions"])
        self.assertFalse(result["promotion_eligible"])

    def test_unpinned_baseline_and_mismatched_task_sets_fail_closed(self):
        baseline = {**self.baseline, "metrics": dict(self.baseline["metrics"])}
        baseline["metrics"]["train-a"] = {**baseline["metrics"]["train-a"], "quality_score": 0.99}
        with self.assertRaisesRegex(ValueError, "pinned baseline"):
            self.evaluate(baseline=baseline)
        candidate = {**self.candidate, "task_ids": self.task_ids[:-1]}
        with self.assertRaisesRegex(ValueError, "same exact task"):
            self.evaluate(candidate=candidate)

    def test_missing_holdout_evidence_and_unscored_holdouts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "exact pinned holdout"):
            self.evaluate(holdout_evidence={"holdout-a": self.holdout_evidence["holdout-a"]})
        unscored = dict(self.holdout_evidence)
        unscored["holdout-a"] = {**unscored["holdout-a"], "scored": False}
        with self.assertRaisesRegex(PermissionError, "lacks an executed score"):
            self.evaluate(holdout_evidence=unscored)

    def test_failed_or_self_attested_verification_cannot_feed_learning(self):
        failed = {**self.receipt, "status": "failed"}
        failed["receipt_digest"] = self.digest({k: v for k, v in failed.items() if k != "receipt_digest"})
        with self.assertRaisesRegex(PermissionError, "failed or unknown verification"):
            self.evaluate(verification_receipt=failed)
        self_attested = {**self.receipt, "worker_identity": "verifier-ci"}
        self_attested["receipt_digest"] = self.digest({k: v for k, v in self_attested.items() if k != "receipt_digest"})
        holdouts = {
            key: {**value, "receipt_digest": self_attested["receipt_digest"]}
            for key, value in self.holdout_evidence.items()
        }
        with self.assertRaisesRegex(PermissionError, "trusted verifier distinct"):
            self.evaluate(verification_receipt=self_attested, holdout_evidence=holdouts)

    def test_learning_cannot_change_policy_authority_or_rollback_controls(self):
        candidate = {**self.candidate, "verification_thresholds": {"minimum_quality": 0.1}}
        with self.assertRaisesRegex(PermissionError, "cannot change safety or authority"):
            self.evaluate(candidate=candidate)
        with self.assertRaises((AttributeError, TypeError)):
            self.gate.policy.minimum_quality = 0.1

    def test_evidence_event_is_idempotent_and_digest_bound(self):
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual(first["candidate_digest"], second["candidate_digest"])
        self.assertEqual(first["journal_event_id"], second["journal_event_id"])
        self.assertEqual(first["journal_event_digest"], second["journal_event_digest"])
        events = [event for event in self.journal.read() if event.state == "graph_learning_evaluated"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["safety_policy_digest"], self.gate.policy_digest)

    def test_candidate_cannot_increase_cost_or_latency_beyond_fixed_policy(self):
        metrics = {
            task_id: {
                "latency_ms": 200.0, "cost_units": 20.0,
                "quality_score": 0.95, "regression_count": 0,
            }
            for task_id in self.task_ids
        }
        result = self.evaluate(candidate={**self.candidate, "metrics": metrics})
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["safety_gates"]["cost_bounded"])
        self.assertFalse(result["safety_gates"]["latency_bounded"])

