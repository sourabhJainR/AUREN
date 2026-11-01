from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.reproducible_experiment import (
    ExperimentConflict, ExperimentManifest, ReproducibleExperimentStore,
)


def manifest():
    return ExperimentManifest(
        experiment_id="exp-1", task_manifest_digest="a" * 64, seed=17,
        split_digest="b" * 64, candidate_digest="c" * 64, baseline_digest="d" * 64,
        harness_digest="e" * 64, environment_digest="f" * 64,
        model_provider="provider-x", model_version="model-2026-11",
    )


class ReproducibleExperimentTests(unittest.TestCase):
    def test_manifest_is_pinned_and_resume_rejects_changed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReproducibleExperimentStore(Path(tmp) / "experiments.sqlite")
            item = manifest()
            digest = store.record_manifest(item)
            store.assert_resume_compatible(item, expected_digest=digest)
            changed = ExperimentManifest(
                experiment_id="exp-1", task_manifest_digest="a" * 64, seed=18,
                split_digest="b" * 64, candidate_digest="c" * 64, baseline_digest="d" * 64,
                harness_digest="e" * 64, environment_digest="f" * 64,
                model_provider="provider-x", model_version="model-2026-11",
            )
            with self.assertRaises(ExperimentConflict):
                store.assert_resume_compatible(changed, expected_digest=digest)

    def test_unknown_or_unscored_outcomes_never_promote_lessons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReproducibleExperimentStore(
                Path(tmp) / "experiments.sqlite", evidence_verifier=lambda *args: True,
            )
            item = manifest()
            store.record_manifest(item)
            store.record_outcome(outcome_id="out-1", manifest=item, status="unknown",
                                 evidence_refs=["external://receipt"], metrics={"score": None})
            with self.assertRaises(PermissionError):
                store.promote_lesson(lesson_id="lesson-1", outcome_id="out-1", lesson={"finding": "x"})

    def test_passed_outcome_requires_trusted_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReproducibleExperimentStore(
                Path(tmp) / "experiments.sqlite",
                evidence_verifier=lambda exp, digest, refs: exp == "exp-1" and len(digest) == 64 and refs == ["external://signed"],
            )
            item = manifest()
            store.record_manifest(item)
            store.record_outcome(outcome_id="out-2", manifest=item, status="passed",
                                 evidence_refs=["external://signed"], metrics={"accuracy": 0.91})
            first = store.promote_lesson(lesson_id="lesson-2", outcome_id="out-2", lesson={"finding": "verified"})
            second = store.promote_lesson(lesson_id="lesson-2", outcome_id="out-2", lesson={"finding": "verified"})
            self.assertEqual(first, second)
            with self.assertRaises(ExperimentConflict):
                store.record_outcome(outcome_id="out-2", manifest=item, status="failed",
                                     evidence_refs=["external://signed"], metrics={"accuracy": 0.91})


    def test_manifest_parent_must_exist_and_ledger_rows_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiments.sqlite"
            store = ReproducibleExperimentStore(path)
            item = manifest()
            with self.assertRaises(ExperimentConflict):
                store.record_manifest(item, parent_digest="9" * 64)
            digest = store.record_manifest(item)
            with sqlite3.connect(path) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "UPDATE experiment_manifests SET manifest_json='{}' WHERE manifest_digest=?",
                        (digest,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "DELETE FROM experiment_manifests WHERE manifest_digest=?",
                        (digest,),
                    )

    def test_lesson_id_cannot_be_rebound_to_another_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReproducibleExperimentStore(
                Path(tmp) / "experiments.sqlite", evidence_verifier=lambda *args: True,
            )
            first = manifest()
            second = ExperimentManifest(
                experiment_id="exp-2", task_manifest_digest="1" * 64, seed=17,
                split_digest="2" * 64, candidate_digest="3" * 64, baseline_digest="4" * 64,
                harness_digest="5" * 64, environment_digest="6" * 64,
                model_provider="provider-x", model_version="model-2026-11",
            )
            store.record_manifest(first)
            store.record_manifest(second)
            store.record_outcome(
                outcome_id="out-first", manifest=first, status="passed",
                evidence_refs=["external://first"], metrics={"score": 1},
            )
            store.record_outcome(
                outcome_id="out-second", manifest=second, status="passed",
                evidence_refs=["external://second"], metrics={"score": 1},
            )
            store.promote_lesson(
                lesson_id="lesson-stable", outcome_id="out-first", lesson={"finding": "same"},
            )
            with self.assertRaises(ExperimentConflict):
                store.promote_lesson(
                    lesson_id="lesson-stable", outcome_id="out-second", lesson={"finding": "same"},
                )

    def test_verifier_must_return_true_and_verifier_errors_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiments.sqlite"
            item = manifest()
            store = ReproducibleExperimentStore(path, evidence_verifier=lambda *args: 1)
            store.record_manifest(item)
            store.record_outcome(
                outcome_id="out-verify", manifest=item, status="passed",
                evidence_refs=["external://signed"], metrics={},
            )
            with self.assertRaises(PermissionError):
                store.promote_lesson(
                    lesson_id="lesson-verify", outcome_id="out-verify", lesson={"finding": "x"},
                )
            failing = ReproducibleExperimentStore(
                path, evidence_verifier=lambda *args: (_ for _ in ()).throw(RuntimeError("verifier down")),
            )
            with self.assertRaises(PermissionError):
                failing.promote_lesson(
                    lesson_id="lesson-verify-2", outcome_id="out-verify", lesson={"finding": "x"},
                )

    def test_outcome_and_lesson_ids_must_be_non_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReproducibleExperimentStore(
                Path(tmp) / "experiments.sqlite", evidence_verifier=lambda *args: True,
            )
            item = manifest()
            store.record_manifest(item)
            with self.assertRaises(ValueError):
                store.record_outcome(
                    outcome_id=" ", manifest=item, status="passed",
                    evidence_refs=[], metrics={},
                )
            with self.assertRaises(ValueError):
                store.promote_lesson(lesson_id=" ", outcome_id="missing", lesson={"finding": "x"})



if __name__ == "__main__":
    unittest.main()
