from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_experiment_controller import AdaptiveExperimentResult
from portable.persistent_experiment_learning import ExperimentManifest, PersistentExperimentLearningStore
from portable.persistent_memory import PersistentMemory
from portable.reproducible_experiment import ReproducibleExperimentStore
from portable.reproducible_learning_graph_bridge import ReproducibleLearningGraphBridge


def canonical_manifest(experiment_id="bridge-exp"):
    return ExperimentManifest(
        experiment_id=experiment_id,
        capability="software-team-collaboration",
        intervention_digest="candidate-intervention-v1",
        task_digest="task-set-v1",
        task_split_digest="holdout-split-v1",
        harness_digest="harness-v1",
        model_id="offline-deterministic-fixture",
        provider_id="local-fixture",
        executor_id="python-3.12",
        evaluator_id="independent-fixture-evaluator",
        policy_digest="policy-v1",
        environment_digest="ubuntu-python-3.12",
        budget_digest="shared-budget-v1",
        isolation_attestation_digest="test-isolation-not-production-attestation",
        seed=41,
    )


def adaptive_result(experiment_id="bridge-exp", **changes):
    values = dict(
        experiment_id=experiment_id,
        intervention_digest="candidate-intervention-v1",
        replication_count=3,
        observation_count=9,
        aggregated_treatment_lift=0.08,
        aggregated_holdout_lift=0.04,
        replication_consistency=1.0,
        regression_detected=False,
        stop=True,
        rollback_recommended=False,
        evidence_sufficient=True,
        reasons=("minimum evidence criteria met",),
    )
    values.update(changes)
    return AdaptiveExperimentResult(**values)


def bridge_for(root: Path):
    memory = PersistentMemory(root / "canonical-memory.sqlite3", require_approval=False)
    learning = PersistentExperimentLearningStore(
        memory, "project-a",
        verify_evidence=lambda refs, manifest, result: bool(refs) and all(
            ref.startswith("arena://") for ref in refs
        ),
        verify_promotion=lambda receipt_id, reviewer_id, ref: ref.startswith("review://"),
        verify_retraction=lambda receipt_id, reason, ref: ref.startswith("retraction://"),
    )
    experiments = ReproducibleExperimentStore(root / "reproducible-experiments.sqlite3")
    return ReproducibleLearningGraphBridge(
        experiment_store=experiments, learning_store=learning,
    ), learning, experiments


class ReproducibleLearningGraphBridgeTests(unittest.TestCase):
    def test_reproducible_manifest_outcome_and_episode_receipt_share_one_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, learning, experiments = bridge_for(Path(tmp))
            manifest = canonical_manifest()
            result = adaptive_result()
            refs = ("arena://campaign/receipt-1",)
            first = bridge.record(
                manifest=manifest, result=result, baseline_digest="b" * 64, seed=41,
                outcome_status="passed", metrics={"task_success": 1.0, "cost_units": 0},
                evidence_refs=refs,
            )
            second = bridge.record(
                manifest=manifest, result=result, baseline_digest="b" * 64, seed=41,
                outcome_status="passed", metrics={"task_success": 1.0, "cost_units": 0},
                evidence_refs=refs,
            )
            self.assertEqual(first.reproducible_manifest_digest, second.reproducible_manifest_digest)
            self.assertEqual(first.reproducible_outcome_digest, second.reproducible_outcome_digest)
            self.assertEqual(first.learning_receipt.receipt_id, second.learning_receipt.receipt_id)
            self.assertEqual("candidate", first.learning_receipt.disposition)

            receipt_node = learning.graph.node_id("learning_receipt", first.learning_receipt.receipt_id)
            incoming = learning.graph.lineage(receipt_node, direction="in")
            self.assertTrue(any(edge.relation == "recorded_as_learning" for edge in incoming))
            outcome_edge = next(edge for edge in incoming if edge.relation == "recorded_as_learning")
            outcome_incoming = learning.graph.lineage(outcome_edge.source_id, direction="in")
            self.assertTrue(any(edge.relation == "produced_outcome" for edge in outcome_incoming))
            manifest_node = learning.graph.node_id(
                "reproducible_experiment_manifest", first.reproducible_manifest_digest,
            )
            self.assertTrue(any(
                edge.relation == "pinned_by"
                for edge in learning.graph.lineage(manifest_node, direction="in")
            ))
            with sqlite3.connect(experiments.path) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM experiment_outcomes WHERE experiment_id=?", ("bridge-exp",)
                ).fetchone()[0])

    def test_unknown_outcome_is_preserved_but_never_becomes_a_positive_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, learning, _ = bridge_for(Path(tmp))
            manifest = canonical_manifest("unknown-exp")
            result = adaptive_result(
                "unknown-exp", replication_count=1, observation_count=3,
                evidence_sufficient=False, reasons=("worker outcome unknown",),
            )
            linked = bridge.record(
                manifest=manifest, result=result, baseline_digest="c" * 64, seed=9,
                outcome_status="unknown", metrics={"reconciled": False},
                evidence_refs=("arena://campaign/unknown-1",),
            )
            self.assertEqual("unscored", linked.learning_receipt.disposition)
            self.assertEqual((), learning.retrieve_compatible(manifest))
            self.assertTrue(linked.reproducible_outcome_digest)

    def test_passed_outcome_cannot_override_insufficient_or_regressed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = bridge_for(Path(tmp))
            manifest = canonical_manifest()
            insufficient = adaptive_result(evidence_sufficient=False, replication_count=1)
            with self.assertRaisesRegex(ValueError, "passed outcome conflicts"):
                bridge.record(
                    manifest=manifest, result=insufficient, baseline_digest="b" * 64, seed=41,
                    outcome_status="passed", metrics={}, evidence_refs=("arena://campaign/1",),
                )
            regressed = adaptive_result(regression_detected=True, rollback_recommended=True)
            with self.assertRaisesRegex(ValueError, "passed outcome conflicts"):
                bridge.record(
                    manifest=manifest, result=regressed, baseline_digest="b" * 64, seed=41,
                    outcome_status="passed", metrics={}, evidence_refs=("arena://campaign/2",),
                )

    def test_manifest_and_result_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = bridge_for(Path(tmp))
            with self.assertRaisesRegex(ValueError, "experiment identity"):
                bridge.record(
                    manifest=canonical_manifest("one"), result=adaptive_result("two"),
                    baseline_digest="b" * 64, seed=41, outcome_status="unknown",
                    metrics={}, evidence_refs=("arena://campaign/1",),
                )


if __name__ == "__main__":
    unittest.main()
