"""Regression tests for the isolated Arena policy subprocess boundary."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from portable.independent_arena import NonceLedger, seal_manifest, verify_receipt
from portable.isolated_arena_runner import run_campaign_isolated


def fixture():
    return seal_manifest({
        "benchmark_id": "isolated-mini-v1",
        "evaluator_id": "evaluator-owned",
        "runtime_id": "policy-process",
        "cases": [
            {"case_id": "case-1", "domain": "arithmetic", "split": "evaluation",
             "prompt": "What is 7 + 8?", "oracle": 15},
            {"case_id": "case-2", "domain": "classification", "split": "sealed_holdout",
             "prompt": "HTTP 503", "oracle": "transient"},
        ],
    })


class IsolatedArenaRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def ledger(self, name):
        return NonceLedger(self.root / f"{name}.sqlite3")

    def run_policy(self, nonce, code, **kwargs):
        return run_campaign_isolated(
            fixture(), [sys.executable, "-c", code],
            policy_digest="sha256:policy-v1", run_nonce=nonce,
            nonce_ledger=self.ledger(nonce), **kwargs
        )

    def test_policy_receives_only_task_fields_and_receipt_verifies(self):
        code = (
            "import json,sys; task=json.load(sys.stdin); "
            "assert set(task)=={'prompt'}; "
            "print(json.dumps(15 if task['prompt']=='What is 7 + 8?' else 'transient'))"
        )
        receipt = self.run_policy("run-1", code)
        self.assertEqual(receipt["counts"], {"pass": 2, "fail": 0, "error": 0})
        self.assertTrue(verify_receipt(receipt, fixture()))

    def test_result_digest_is_stable_but_receipts_are_unique(self):
        code = "import json,sys; x=json.load(sys.stdin); print(json.dumps(15 if x['prompt']=='What is 7 + 8?' else 'transient'))"
        first = self.run_policy("run-a", code)
        second = self.run_policy("run-b", code)
        self.assertEqual(first["result_digest"], second["result_digest"])
        self.assertNotEqual(first["receipt_digest"], second["receipt_digest"])

    def test_malformed_output_and_timeout_are_recorded_as_errors(self):
        malformed = self.run_policy("bad-json", "print('not-json')")
        self.assertEqual(malformed["counts"]["error"], 2)
        timed_out = self.run_policy(
            "timeout", "import time; time.sleep(5); print('null')", timeout_seconds=0.01
        )
        self.assertEqual(timed_out["counts"]["error"], 2)
        self.assertTrue(verify_receipt(timed_out, fixture()))

    def test_campaign_deadline_bounds_multiple_policy_invocations(self):
        receipt = self.run_policy(
            "campaign-deadline",
            "import time; time.sleep(0.2); print('null')",
            timeout_seconds=1.0,
            campaign_timeout_seconds=0.05,
        )
        self.assertGreater(receipt["counts"]["error"], 0)
        self.assertTrue(verify_receipt(receipt, fixture()))

    def test_oversized_policy_output_is_rejected_without_capture(self):
        receipt = self.run_policy(
            "oversized-output",
            "print('x' * 100000)",
            max_output_bytes=128,
        )
        self.assertEqual(receipt["counts"]["error"], 2)
        self.assertTrue(verify_receipt(receipt, fixture()))

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_timeout_kills_policy_descendants(self):
        side_effect = self.root / "escaped-child.txt"
        child_code = (
            "import time,pathlib; time.sleep(0.4); "
            + "pathlib.Path(" + repr(str(side_effect)) + ").write_text('escaped')"
        )
        parent_code = (
            "import subprocess,sys,time; "
            + "subprocess.Popen([sys.executable, '-c', " + repr(child_code) + "]); "
            + "time.sleep(5); print('null')"
        )
        receipt = self.run_policy(
            "timeout-descendant", parent_code, timeout_seconds=0.05,
            campaign_timeout_seconds=2.0,
        )
        self.assertEqual(receipt["counts"]["error"], 2)
        time.sleep(0.6)
        self.assertFalse(side_effect.exists(), "policy descendant survived timeout cleanup")

    def test_invalid_resource_budgets_are_rejected(self):
        code = "print('null')"
        with self.assertRaisesRegex(ValueError, "campaign_timeout_seconds"):
            self.run_policy("bad-campaign-budget", code, campaign_timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "max_output_bytes"):
            self.run_policy("bad-output-budget", code, max_output_bytes=0)

    def test_policy_environment_does_not_inherit_evaluator_secrets(self):
        os.environ["AUREN_EVALUATOR_SECRET_FOR_TEST"] = "do-not-leak"
        self.addCleanup(os.environ.pop, "AUREN_EVALUATOR_SECRET_FOR_TEST", None)
        code = "import json,os; print(json.dumps('AUREN_EVALUATOR_SECRET_FOR_TEST' in os.environ))"
        receipt = self.run_policy("env-check", code)
        self.assertEqual(receipt["counts"], {"pass": 0, "fail": 2, "error": 0})

    def test_sensitive_policy_environment_overrides_are_rejected(self):
        code = "print('null')"
        with self.assertRaisesRegex(ValueError, "sensitive environment variable"):
            self.run_policy("bad-env", code, policy_environment={"ORACLE_ANSWER": "gold"})

    def test_duplicate_nonce_is_rejected_by_persistent_ledger(self):
        code = "import json,sys; x=json.load(sys.stdin); print(json.dumps(15 if x['case_id']=='case-1' else 'transient'))"
        sealed = fixture()
        ledger = self.ledger("shared")
        run_campaign_isolated(
            sealed, [sys.executable, "-c", code], policy_digest="p",
            run_nonce="one-use", nonce_ledger=ledger,
        )
        with self.assertRaises(ValueError):
            run_campaign_isolated(
                sealed, [sys.executable, "-c", code], policy_digest="p",
                run_nonce="one-use", nonce_ledger=ledger,
            )


if __name__ == "__main__":
    unittest.main()
