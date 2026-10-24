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
        self.runtime = AgencyEvolutionRuntime(Path(self.tmp.name) / "evolution.sqlite3",
                                              max_attempts=2, max_cycles=2)

    def queued(self, experiment_id="exp-1", candidate="candidate-1", parent=""):
        return self.runtime.create_experiment(manifest(experiment_id, candidate),
                                              evidence_refs=("trace:42",), parent_operation_id=parent)

    def passing_evaluation(self, operation_id):
        self.runtime.claim(operation_id)
        return self.runtime.record_evaluation(
            operation_id, outcome="pass", evidence_refs=("evaluation:independent:1",),
            evaluator_revision="evaluator-v1", independent=True, holdout_isolated=True,
            metrics={"quality": 0.98, "latency_ms": 120}, domain="python",
        )

    def gates(self):
        return {
            "evaluator_independent": True, "holdout_isolated": True,
            "evidence_integrity": True, "required_checks_passed": True,
            "rollback_proven": True, "approval_recorded": True,
        }

    def authorize_for_canary(self, operation_id):
        decision = self.runtime.promotion_decision(
            operation_id, gates=self.gates(), regression_passed=True, risk_approved=True,
        )
        self.assertEqual(decision["action"], "awaiting_approval")
        self.runtime.record_release_event(operation_id, event="shadow_passed",
                                          evidence_ref="shadow:verified:1",
                                          controller_authorized=True, verified=True)
        self.runtime.record_release_event(operation_id, event="approval_verified",
                                          evidence_ref="approval:human:1",
                                          controller_authorized=True, verified=True)

    def complete_release(self, operation_id):
        self.authorize_for_canary(operation_id)
        self.runtime.record_release_event(operation_id, event="canary_started",
                                          evidence_ref="canary:start:1", controller_authorized=True)
        self.runtime.record_release_event(operation_id, event="canary_passed",
                                          evidence_ref="canary:verified:1",
                                          controller_authorized=True, verified=True)
        return self.runtime.record_release_event(operation_id, event="promoted",
                                                 evidence_ref="release:verified:1",
                                                 controller_authorized=True, verified=True)

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
        self.assertEqual(self.runtime.recover_interrupted(), [item["operation_id"]])
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
                                              evidence_ref="canary:start", controller_authorized=True)
        self.authorize_for_canary(item["operation_id"])
        self.runtime.record_release_event(item["operation_id"], event="canary_started",
                                          evidence_ref="canary:start", controller_authorized=True)
        with self.assertRaisesRegex(ValueError, "prior verified event"):
            self.runtime.record_release_event(item["operation_id"], event="promoted",
                                              evidence_ref="release:1", controller_authorized=True,
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
            controller_authorized=False, verified=True,
        )
        self.assertEqual(result["state"], "blocked")

    def test_trace_ingestion_returns_safe_prioritized_signals(self):
        result = self.runtime.ingest_trace({
            "trace_id": "trace-1", "status": "failed",
            "spans": [{"span_id": "s1", "status": "failed", "output": "secret payload"}],
        })
        self.assertEqual(result["signal_count"], 2)
        self.assertTrue(all("secret payload" not in str(signal) for signal in result["signals"]))


if __name__ == "__main__":
    unittest.main()
