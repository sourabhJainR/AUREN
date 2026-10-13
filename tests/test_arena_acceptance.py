import tempfile
import unittest
from pathlib import Path

from portable.arena_acceptance import assess_receipt
from portable.independent_arena import NonceLedger, run_campaign, seal_manifest


def fixture():
    return seal_manifest({
        "benchmark_id": "acceptance-gate-v1",
        "evaluator_id": "independent-evaluator",
        "runtime_id": "candidate-runtime",
        "cases": [
            {"case_id": "eval-1", "domain": "logic", "split": "evaluation", "prompt": "one", "oracle": 1},
            {"case_id": "holdout-1", "domain": "logic", "split": "sealed_holdout", "prompt": "two", "oracle": 2},
        ],
    })


class ArenaAcceptanceTests(unittest.TestCase):
    def run_receipt(self, policy, nonce):
        self.temporary_directory = tempfile.TemporaryDirectory()
        ledger = NonceLedger(Path(self.temporary_directory.name) / "ledger.sqlite3")
        sealed = fixture()
        receipt = run_campaign(
            sealed, policy, nonce_ledger=ledger,
            policy_digest="sha256:candidate", run_nonce=nonce,
        )
        return sealed, receipt

    def tearDown(self):
        if hasattr(self, "temporary_directory"):
            self.temporary_directory.cleanup()

    def test_only_threshold_passing_error_free_receipt_is_accepted(self):
        sealed, receipt = self.run_receipt(lambda task: 1 if task["prompt"] == "one" else 2, "pass")
        result = assess_receipt(receipt, sealed, minimum_overall_pass_rate=1.0, minimum_holdout_pass_rate=1.0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["reason"], "accepted")

    def test_valid_receipt_with_policy_errors_is_not_accepted(self):
        sealed, receipt = self.run_receipt(lambda _task: (_ for _ in ()).throw(RuntimeError("failure")), "error")
        result = assess_receipt(receipt, sealed, minimum_overall_pass_rate=0.0, minimum_holdout_pass_rate=0.0)
        self.assertFalse(result["accepted"])
        self.assertIn("case_errors_present", result["reason"])

    def test_holdout_threshold_is_enforced_independently(self):
        sealed, receipt = self.run_receipt(lambda task: 1 if task["prompt"] == "one" else 0, "holdout-fail")
        result = assess_receipt(receipt, sealed, minimum_overall_pass_rate=0.5, minimum_holdout_pass_rate=1.0)
        self.assertFalse(result["accepted"])
        self.assertIn("holdout_threshold_not_met", result["reason"])

    def test_tampered_receipt_is_rejected(self):
        sealed, receipt = self.run_receipt(lambda task: 1 if task["prompt"] == "one" else 2, "tamper")
        receipt["counts"]["pass"] = 99
        result = assess_receipt(receipt, sealed)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "receipt_invalid")

    def test_thresholds_must_be_finite_probabilities(self):
        sealed, receipt = self.run_receipt(lambda task: 1 if task["prompt"] == "one" else 2, "threshold")
        for value in (-0.1, 1.1, float("nan"), True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    assess_receipt(receipt, sealed, minimum_holdout_pass_rate=value)


if __name__ == "__main__":
    unittest.main()
