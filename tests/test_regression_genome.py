import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness" / "runtime"))

from regression_genome import candidate_case, evaluate_promotion


class RegressionGenomeTests(unittest.TestCase):
    def candidate(self):
        return candidate_case(
            "accepted_bugfix",
            "evidence://bugfix/123",
            "provider timeout was retried without reconciliation",
            "ambiguous provider outcome remains blocked until lookup verifies it",
            ["no duplicate dispatch", "retain receipt evidence"],
            proposer_id="engineer-a",
        )

    def test_candidate_is_stable_and_never_mandatory_on_creation(self):
        first = self.candidate()
        second = self.candidate()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("candidate", first["status"])
        self.assertFalse(first["mandatory"])
        self.assertTrue(first["promotion_requirements"]["human_approval"])

    def test_candidate_rejects_credential_like_content(self):
        with self.assertRaisesRegex(ValueError, "credential-like"):
            candidate_case(
                "user_correction",
                "evidence://correction/1",
                "API key leaked",
                "api_key=sk_live_abcdefghijklmnop",
                proposer_id="engineer-a",
            )

    def test_promotion_fails_closed_without_verified_evidence(self):
        candidate = self.candidate()
        runs = [
            {"run_id": "run-1", "case_id": candidate["id"], "passed": True, "observed": candidate["expected"], "evidence_ref": "run://1"},
            {"run_id": "run-2", "case_id": candidate["id"], "passed": True, "observed": candidate["expected"], "evidence_ref": "run://2"},
        ]
        holdout = {
            "case_id": candidate["id"], "passed": True, "sealed": True,
            "dataset_id": "holdout-2026-10", "evaluator_id": "evaluator-b",
            "evidence_ref": "holdout://case/1",
        }
        approval = {"approved": True, "reviewer_id": "reviewer-b", "evidence_ref": "approval://case/1"}
        result = evaluate_promotion(
            candidate, runs, holdout, approval,
            training_dataset_id="train-2026-10",
        )
        self.assertFalse(result["promoted"])
        self.assertFalse(result["mandatory"])
        self.assertIn("deterministic_run_evidence_not_verified", result["reasons"])
        self.assertIn("holdout_evidence_not_verified", result["reasons"])
        self.assertIn("approval_evidence_not_verified", result["reasons"])

    def test_promotion_requires_two_runs_independent_holdout_and_human_approval(self):
        candidate = self.candidate()
        runs = [
            {"run_id": "run-1", "case_id": candidate["id"], "passed": True, "observed": candidate["expected"], "evidence_ref": "run://1"},
            {"run_id": "run-2", "case_id": candidate["id"], "passed": True, "observed": candidate["expected"], "evidence_ref": "run://2"},
        ]
        holdout = {
            "case_id": candidate["id"], "passed": True, "sealed": True,
            "dataset_id": "holdout-2026-10", "evaluator_id": "evaluator-b",
            "evidence_ref": "holdout://case/1",
        }
        approval = {"approved": True, "reviewer_id": "reviewer-c", "evidence_ref": "approval://case/1"}
        verified = {
            ("evidence://bugfix/123", "candidate_source"),
            ("run://1", "deterministic_run"),
            ("run://2", "deterministic_run"),
            ("holdout://case/1", "holdout"),
            ("approval://case/1", "human_approval"),
        }
        result = evaluate_promotion(
            candidate, runs, holdout, approval,
            training_dataset_id="train-2026-10",
            evidence_verifier=lambda ref, kind: (ref, kind) in verified,
        )
        self.assertTrue(result["promoted"])
        self.assertTrue(result["mandatory"])
        self.assertEqual("promoted", result["status"])

    def test_same_dataset_and_duplicate_runs_block_promotion(self):
        candidate = self.candidate()
        run = {"run_id": "run-1", "case_id": candidate["id"], "passed": True, "observed": candidate["expected"], "evidence_ref": "run://1"}
        result = evaluate_promotion(
            candidate, [run, dict(run)],
            {"case_id": candidate["id"], "passed": True, "sealed": True, "dataset_id": "train", "evaluator_id": "evaluator-b", "evidence_ref": "holdout://1"},
            {"approved": True, "reviewer_id": "reviewer-b", "evidence_ref": "approval://1"},
            training_dataset_id="train",
            evidence_verifier=lambda *_: True,
        )
        self.assertFalse(result["promoted"])
        self.assertIn("two_distinct_deterministic_runs_required", result["reasons"])
        self.assertIn("holdout_dataset_must_be_independent", result["reasons"])


if __name__ == "__main__":
    unittest.main()
