from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.adaptive_experiment_controller import AdaptiveExperimentResult
from portable.agency_observability import Trace
from portable.experiment_learning_bridge import ExperimentLearningBridge
from portable.persistent_experiment_learning import (
    ExperimentManifest,
    PersistentExperimentLearningStore,
)
from portable.persistent_memory import PersistentMemory


def _manifest() -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="trace-exp-1", capability="planner",
        intervention_digest="intervention", task_digest="task",
        task_split_digest="split", harness_digest="harness",
        model_id="model", provider_id="provider", executor_id="executor",
        evaluator_id="independent-evaluator", policy_digest="policy",
        environment_digest="environment", budget_digest="budget",
        isolation_attestation_digest="attestation", seed=7,
    )


def _result() -> AdaptiveExperimentResult:
    return AdaptiveExperimentResult(
        experiment_id="trace-exp-1", intervention_digest="intervention",
        replication_count=3, observation_count=9,
        aggregated_treatment_lift=0.08, aggregated_holdout_lift=0.04,
        replication_consistency=1.0, regression_detected=False, stop=True,
        rollback_recommended=False, evidence_sufficient=True,
        reasons=("replicated",),
    )


class PersistentLearningObservabilityTests(unittest.TestCase):
    def test_verification_and_learning_writes_are_parented_and_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = PersistentMemory(Path(tmp) / "memory.sqlite3", require_approval=False)
            store = PersistentExperimentLearningStore(
                memory, "project",
                verify_evidence=lambda refs, manifest, result: True,
                verify_promotion=lambda receipt, reviewer, ref: True,
                verify_retraction=lambda receipt, reason, ref: True,
            )
            manifest = _manifest()
            result = _result()
            signal = ExperimentLearningBridge().derive(capability=manifest.capability, result=result)
            trace = Trace("learning lifecycle", run_id="run-1", task_id="task-1")
            parent = trace.span("supervisor", phase="supervisor")

            receipt = store.record(
                manifest=manifest, result=result, signal=signal,
                evidence_refs=("arena:receipt-1",), trace=trace,
                parent_span_id=parent.span_id,
            )
            store.promote_candidate(
                receipt.receipt_id, reviewer_id="independent-reviewer",
                evidence_ref="review:receipt-1", trace=trace,
                parent_span_id=parent.span_id,
            )

            observed = {span.name: span for span in trace.spans[1:]}
            self.assertEqual(
                {
                    "learning.evidence_verification",
                    "learning.persistence",
                    "learning.promotion_evidence_verification",
                    "learning.promotion_persistence",
                },
                set(observed),
            )
            for span in observed.values():
                self.assertEqual(parent.span_id, span.parent_id)
                self.assertIsNotNone(span.end)
            self.assertEqual("ok", observed["learning.evidence_verification"].status)
            self.assertEqual("ok", observed["learning.persistence"].status)
            self.assertEqual("ok", observed["learning.promotion_evidence_verification"].status)
            self.assertEqual("ok", observed["learning.promotion_persistence"].status)
            serialized = str(trace.as_dict())
            self.assertNotIn("arena:receipt-1", serialized)
            self.assertNotIn("review:receipt-1", serialized)

    def test_unavailable_evidence_verification_is_visible_as_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = PersistentMemory(Path(tmp) / "memory.sqlite3", require_approval=False)
            store = PersistentExperimentLearningStore(
                memory, "project",
                verify_evidence=lambda refs, manifest, result: False,
                verify_promotion=lambda receipt, reviewer, ref: True,
                verify_retraction=lambda receipt, reason, ref: True,
            )
            manifest = _manifest()
            result = _result()
            signal = ExperimentLearningBridge().derive(capability=manifest.capability, result=result)
            trace = Trace("unverified learning")
            parent = trace.span("supervisor", phase="supervisor")
            receipt = store.record(
                manifest=manifest, result=result, signal=signal,
                evidence_refs=("arena:unverified",), trace=trace,
                parent_span_id=parent.span_id,
            )
            verification = next(span for span in trace.spans if span.name == "learning.evidence_verification")
            self.assertEqual("unscored", verification.status)
            self.assertEqual("unscored", receipt.disposition)
            self.assertFalse(receipt.evidence_verified)
            self.assertNotIn("arena:unverified", str(trace.as_dict()))


if __name__ == "__main__":
    unittest.main()
