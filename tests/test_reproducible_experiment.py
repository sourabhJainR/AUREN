from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
