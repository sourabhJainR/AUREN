"""Durable lifecycle and fail-closed tests for agency evolution Gates F-I."""
import tempfile
import unittest
from pathlib import Path

from portable.agency_evolution_gates import ExperimentManifest
from portable.agency_evolution_runtime import AgencyEvolutionRuntime, EvolutionConflict


def manifest(experiment_id="exp-1", candidate="candidate-1"):
    return ExperimentManifest(
        experiment_id=experiment_id, hypothesis_id="hyp-1",
        baseline_revision="a" * 40, candidate_revision=candidate,
        dataset_digest="dataset-digest", evaluator_revision="evaluator-v1",
        seed=17, budget_units=5, metrics=("quality", "latency"),
        holdout_reference="holdout-ref-v1", holdout_payload_included=False,
    )


class AgencyEvolutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = AgencyEvolutionRuntime(
            Path(self.tmp.name) / "evolution.sqlite3", max_attempts=2, max_cycles=10,
            release_evidence_verifier=lambda operation_id, event, evidence_ref, verified:
                not evidence_ref.startswith("untrusted:"),
        )

    def queued(self, experiment_id="exp-1", candidate="candidate-1", parent=""):
        return self.runtime.create_experiment(manifest(experiment_id, candidate),
                                              evidence_refs=("trace:42",), parent_operation_id=parent)

    def passing_evaluation(self, operation_id):
        self.runtime.claim(operation_id)
        result = self.runtime.record_evaluation(
            operation_id, outcome="pass", evidence_refs=("evaluation:independent:1",),
            evaluator_revision="evaluator-v1", independent=True, holdout_isolated=True,
            metrics={"quality": 0.98, "latency_ms": 120}, domain="python",
        )
        cases = tuple([(True, True)] * 10 + [(False, False)] * 10)
        calibration = self.runtime.calibrate_evaluator(
            operation_id, evaluator_revision="evaluator-v1", adjudicated_cases=cases,
            adjudication_evidence_refs=("adjudication:independent:1",),
            independent_adjudication=True,
        )
        self.assertTrue(calibration["accepted"])
        return result

    def gates(self):
        return {
            "evaluator_independent": True, "holdout_isolated": True,
            "evidence_integrity": True, "required_checks_passed": True,
            "rollback_proven": True, "approval_recorded": True,
        }

    def authorize_for_canary(self, operation_id):
        if self.runtime.snapshot(operation_id)["state"] == "evaluated":
            decision = self.runtime.promotion_decision(
                operation_id, gates=self.gates(), regression_passed=True, risk_approved=True,
            )
            self.assertEqual(decision["action"], "awaiting_approval")
        else:
            self.assertEqual(self.runtime.snapshot(operation_id)["state"], "awaiting_approval")
        self.runtime.record_release_event(operation_id, event="shadow_passed",
                                          evidence_ref="shadow:verified:1",
                                          verified=True)
        self.runtime.record_release_event(operation_id, event="approval_verified",
                                          evidence_ref="approval:human:1",
                                          verified=True)

    def complete_release(self, operation_id):
        self.authorize_for_canary(operation_id)
        self.runtime.record_release_event(operation_id, event="canary_started",
                                          evidence_ref="canary:start:1", )
        self.runtime.record_release_event(operation_id, event="canary_passed",
                                          evidence_ref="canary:verified:1",
                                          verified=True)
        return self.runtime.record_release_event(operation_id, event="promoted",
                                                 evidence_ref="release:verified:1",
                                                 verified=True)

    def test_stable_operation_id_and_conflicting_immutable_manifest(self):
        first = self.queued()
        again = self.queued()
        self.assertEqual(first["operation_id"], again["operation_id"])
        with self.assertRaises(EvolutionConflict):
            self.queued(candidate="b" * 40)

    def test_interrupted_operation_blocks_until_remote_reconciliation(self):
        item = self.queued()
        running = self.runtime.claim(item["operation_id"])
        self.assertEqual(running["state"], "running")
        self.assertEqual(self.runtime.recover_interrupted(older_than_seconds=0), [item["operation_id"]])
        blocked = self.runtime.snapshot(item["operation_id"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["outcome"], "unknown")
        with self.assertRaisesRegex(ValueError, "cannot be claimed"):
            self.runtime.claim(item["operation_id"])
        still_unknown = self.runtime.reconcile(item["operation_id"], remote_state="unknown",
                                               evidence_ref="provider:lookup:1")
        self.assertEqual(still_unknown["state"], "blocked")
        queued = self.runtime.reconcile(item["operation_id"], remote_state="not_started",
                                        evidence_ref="provider:lookup:2")
        self.assertEqual(queued["state"], "queued")
        self.assertTrue(self.runtime.verify_event_chain())

    def test_unknown_unscored_and_inconclusive_never_become_success(self):
        for outcome in ("unknown", "unscored", "inconclusive"):
            item = self.queued("exp-" + outcome)
            self.runtime.claim(item["operation_id"])
            result = self.runtime.record_evaluation(
                item["operation_id"], outcome=outcome, evidence_refs=("eval:1",),
                evaluator_revision="evaluator-v1", independent=True, holdout_isolated=True,
                metrics={}, domain="python",
            )
            self.assertNotEqual(result["state"], "evaluated")
            self.assertIsNot(self.runtime.promotion_decision(
                item["operation_id"], gates=self.gates(), regression_passed=True,
                risk_approved=True,
            )["action"], "awaiting_approval")

    def test_independence_and_holdout_flags_are_required(self):
        item = self.queued()
        self.runtime.claim(item["operation_id"])
        result = self.runtime.record_evaluation(
            item["operation_id"], outcome="pass", evidence_refs=("eval:1",),
            evaluator_revision="evaluator-v1", independent=False, holdout_isolated=True,
            metrics={}, domain="python",
        )
        self.assertEqual(result["state"], "blocked")
        self.assertFalse(result["result"]["verified_pass"])

    def test_release_order_cannot_skip_approval_or_canary(self):
        item = self.queued()
        self.passing_evaluation(item["operation_id"])
        self.runtime.promotion_decision(item["operation_id"], gates=self.gates(),
                                        regression_passed=True, risk_approved=True)
        with self.assertRaisesRegex(ValueError, "prior verified event"):
            self.runtime.record_release_event(item["operation_id"], event="canary_started",
                                              evidence_ref="canary:start", )
        self.authorize_for_canary(item["operation_id"])
        self.runtime.record_release_event(item["operation_id"], event="canary_started",
                                          evidence_ref="canary:start", )
        with self.assertRaisesRegex(ValueError, "prior verified event"):
            self.runtime.record_release_event(item["operation_id"], event="promoted",
                                              evidence_ref="release:1", 
                                              verified=True)

    def test_two_audited_cycles_require_verified_parent_evidence(self):
        parent = self.queued()
        self.passing_evaluation(parent["operation_id"])
        self.complete_release(parent["operation_id"])
        child = self.queued("exp-2", "candidate-2", parent=parent["operation_id"])
        self.assertIn("operation:" + parent["operation_id"], child["evidence_refs"])
        self.passing_evaluation(child["operation_id"])
        self.complete_release(child["operation_id"])
        self.assertTrue(self.runtime.verify_event_chain())
        self.assertEqual(self.runtime.snapshot(child["operation_id"])["state"], "completed")

    def test_follow_on_cycle_rejected_when_parent_is_not_verified(self):
        parent = self.queued()
        with self.assertRaisesRegex(ValueError, "verified successful evidence"):
            self.queued("exp-2", "candidate-2", parent=parent["operation_id"])

    def test_untrusted_release_action_blocks_and_does_not_claim_success(self):
        item = self.queued()
        self.passing_evaluation(item["operation_id"])
        self.runtime.promotion_decision(item["operation_id"], gates=self.gates(),
                                        regression_passed=True, risk_approved=True)
        result = self.runtime.record_release_event(
            item["operation_id"], event="shadow_passed", evidence_ref="untrusted:1",
            verified=True,
        )
        self.assertEqual(result["state"], "blocked")



    def test_promotion_requires_accepted_calibration_and_clean_drift(self):
        item = self.queued()
        self.runtime.claim(item["operation_id"])
        self.runtime.record_evaluation(
            item["operation_id"], outcome="pass", evidence_refs=("eval:1",),
            evaluator_revision="evaluator-v1", independent=True, holdout_isolated=True,
            metrics={"quality": 0.98}, domain="python",
        )
        blocked = self.runtime.promotion_decision(
            item["operation_id"], gates=self.gates(), regression_passed=True, risk_approved=True,
        )
        self.assertEqual(blocked["action"], "block")
        self.assertIn("calibration", blocked["reason"])
        cases = tuple([(True, True)] * 10 + [(False, False)] * 10)
        self.runtime.calibrate_evaluator(
            item["operation_id"], evaluator_revision="evaluator-v1", adjudicated_cases=cases,
            adjudication_evidence_refs=("adjudication:1",), independent_adjudication=True,
        )
        self.runtime.assess_drift(
            item["operation_id"], metric="quality", observations=(0.7, 0.71),
            fixed_release_baseline=0.95, adaptive_baseline=0.7,
            absolute_tolerance=0.05, adaptive_tolerance=0.1, evidence_ref="metrics:1",
        )
        blocked_drift = self.runtime.promotion_decision(
            item["operation_id"], gates=self.gates(), regression_passed=True, risk_approved=True,
        )
        self.assertEqual(blocked_drift["action"], "block")
        self.assertIn("drift", blocked_drift["reason"])

    def test_calibration_rejects_non_independent_adjudication(self):
        item = self.queued()
        with self.assertRaisesRegex(ValueError, "independent adjudication"):
            self.runtime.calibrate_evaluator(
                item["operation_id"], evaluator_revision="evaluator-v1",
                adjudicated_cases=((True, True),), adjudication_evidence_refs=("source:1",),
                independent_adjudication=False,
            )

    def test_cycle_budget_is_enforced(self):
        bounded = AgencyEvolutionRuntime(Path(self.tmp.name) / "bounded.sqlite3", max_cycles=1)
        bounded.create_experiment(manifest("bounded-1"), evidence_refs=("trace:1",))
        with self.assertRaisesRegex(ValueError, "cycle budget exhausted"):
            bounded.create_experiment(manifest("bounded-2", "b" * 40), evidence_refs=("trace:2",))


    def test_release_is_blocked_when_no_trusted_verifier_is_configured(self):
        unverified_runtime = AgencyEvolutionRuntime(Path(self.tmp.name) / "no-verifier.sqlite3")
        item = unverified_runtime.create_experiment(manifest(), evidence_refs=("trace:1",))
        result = unverified_runtime.record_release_event(
            item["operation_id"], event="shadow_passed", evidence_ref="shadow:1", verified=True,
        )
        self.assertEqual(result["state"], "blocked")
        self.assertIn("trusted controller authorization missing", result["next_action"])

    def test_trace_ingestion_returns_safe_prioritized_signals(self):
        result = self.runtime.ingest_trace({
            "trace_id": "trace-1", "status": "failed",
            "spans": [{"span_id": "s1", "status": "failed", "output": "secret payload"}],
        })
        self.assertEqual(result["signal_count"], 2)
        self.assertTrue(all("secret payload" not in str(signal) for signal in result["signals"]))


if __name__ == "__main__":
    unittest.main()
