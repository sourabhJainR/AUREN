from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.adaptive_experiment_controller import ExperimentController, ExperimentReplication
from portable.campaign_intervention_loop import CampaignInterventionPlanner
from portable.experiment_learning_cycle import ExperimentLearningCycle
from portable.external_evaluation_campaign import LearningIntervention
from portable.persistent_experiment_learning import (
    ExperimentManifest,
    LearningEvidenceError,
    LearningStoreConflict,
    PersistentExperimentLearningStore,
)
from portable.persistent_memory import PersistentMemory


def intervention():
    return LearningIntervention(
        "intervention-1", "planner", "improve plan reliability",
        "use evidence-backed plan selection", "unchanged-policy", "holdout regression",
    )


def manifest(experiment_id="experiment-1", *, intervention_digest=None, **changes):
    i = intervention()
    values = dict(
        experiment_id=experiment_id,
        capability="planner",
        intervention_digest=intervention_digest or i.intervention_digest,
        task_digest="task-digest",
        task_split_digest="sealed-task-split",
        harness_digest="harness-v4",
        model_id="model-a",
        provider_id="provider-a",
        executor_id="executor-a",
        evaluator_id="evaluator-a",
        policy_digest="policy-digest",
        environment_digest="environment-digest",
        budget_digest="budget-digest",
        isolation_attestation_digest="isolation-attestation",
        seed=7,
    )
    values.update(changes)
    return ExperimentManifest(**values)


def replications(*, regression=False, one_only=False):
    count = 1 if one_only else 3
    return tuple(
        ExperimentReplication(
            campaign_digest=chr(ord("c") + index) * 64,
            domain="planning" if index < 2 else "tools",
            control_scores=(0.50, 0.50),
            treatment_scores=(0.52, 0.52) if one_only else (0.57, 0.58),
            holdout_scores=(0.30, 0.30) if regression else ((0.51, 0.51) if one_only else (0.53, 0.54)),
            verified=True,
            independent_oracle=True,
            contamination_detected=False,
        )
        for index in range(count)
    )


def make_cycle(tmp_path, *, evidence=True):
    memory = PersistentMemory(Path(tmp_path) / "memory.sqlite3", require_approval=False)
    store = PersistentExperimentLearningStore(
        memory, "project-a",
        verify_evidence=lambda refs, m, r: evidence and bool(refs) and all(x.startswith("arena-run:") for x in refs),
        verify_promotion=lambda receipt_id, reviewer_id, ref: ref.startswith("review:"),
        verify_retraction=lambda receipt_id, reason, ref: ref.startswith("retraction:"),
    )
    return ExperimentLearningCycle(controller=ExperimentController(), learning_store=store), store


class ExperimentLearningCycleTests(unittest.TestCase):
    def test_aggregate_is_persisted_before_cycle_returns_and_requires_promotion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, store = make_cycle(tmp)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            outcome = cycle.run(
                manifest=m, intervention=i, replications=replications(),
                retest_contract=contract, baseline_score=0.50,
                evidence_refs=("arena-run:campaign-1",),
            )
            self.assertTrue(outcome.experiment_result.evidence_sufficient)
            self.assertEqual("causal-review", outcome.signal.action)
            self.assertEqual("candidate", outcome.receipt.disposition)
            self.assertEqual((), store.retrieve_compatible(m))
            store.promote_candidate(
                outcome.receipt.receipt_id, reviewer_id="independent-reviewer", evidence_ref="review:1",
            )
            self.assertEqual((outcome.receipt.receipt_id,), tuple(
                item.receipt_id for item in store.retrieve_compatible(m)
            ))

    def test_cycle_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, _ = make_cycle(tmp)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            kwargs = dict(
                manifest=m, intervention=i, replications=replications(),
                retest_contract=contract, baseline_score=0.50,
                evidence_refs=("arena-run:campaign-1",),
            )
            first = cycle.run(**kwargs)
            second = cycle.run(**kwargs)
            self.assertEqual(first.receipt.receipt_id, second.receipt.receipt_id)
            self.assertEqual(first.cycle_digest, second.cycle_digest)

    def test_rollback_result_is_persisted_as_negative_not_positive(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, store = make_cycle(tmp)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            outcome = cycle.run(
                manifest=m, intervention=i, replications=replications(regression=True),
                retest_contract=contract, baseline_score=0.50,
                evidence_refs=("arena-run:campaign-regression",),
            )
            self.assertTrue(outcome.experiment_result.rollback_recommended)
            self.assertEqual("rollback", outcome.signal.action)
            self.assertEqual("negative", outcome.receipt.disposition)
            with self.assertRaises(LearningEvidenceError):
                store.promote_candidate(outcome.receipt.receipt_id, reviewer_id="reviewer", evidence_ref="review:1")
            self.assertEqual((), store.retrieve_compatible(m))

    def test_insufficient_replication_is_recorded_as_unscored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, store = make_cycle(tmp)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            outcome = cycle.run(
                manifest=m, intervention=i, replications=replications(one_only=True),
                retest_contract=contract, baseline_score=0.50,
                evidence_refs=("arena-run:small-sample",),
            )
            self.assertFalse(outcome.experiment_result.evidence_sufficient)
            self.assertEqual("insufficient-evidence", outcome.signal.action)
            self.assertEqual("unscored", outcome.receipt.disposition)
            self.assertEqual((), store.retrieve_compatible(m))

    def test_rejected_evidence_never_returns_a_cycle_result(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, store = make_cycle(tmp, evidence=False)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            outcome = cycle.run(
                manifest=m, intervention=i, replications=replications(),
                retest_contract=contract, baseline_score=0.50,
                evidence_refs=("arena-run:untrusted",),
            )
            self.assertEqual("unscored", outcome.receipt.disposition)
            self.assertEqual((), store.retrieve_compatible(m))

    def test_manifest_mismatch_fails_before_aggregation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, _ = make_cycle(tmp)
            i = intervention()
            m = manifest(intervention_digest="wrong-intervention-digest")
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            with self.assertRaisesRegex(LearningStoreConflict, "does not match intervention"):
                cycle.run(
                    manifest=m, intervention=i, replications=replications(),
                    retest_contract=contract, baseline_score=0.50,
                    evidence_refs=("arena-run:campaign-1",),
                )

    def test_empty_evidence_refs_fail_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cycle, _ = make_cycle(tmp)
            i = intervention()
            m = manifest()
            contract = CampaignInterventionPlanner().retest_contract("p" * 64, i)
            with self.assertRaisesRegex(LearningEvidenceError, "evidence references"):
                cycle.run(
                    manifest=m, intervention=i, replications=replications(),
                    retest_contract=contract, baseline_score=0.50,
                    evidence_refs=(),
                )


if __name__ == "__main__":
    unittest.main()
