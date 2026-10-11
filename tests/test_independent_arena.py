import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portable.independent_arena import ArenaError, NonceLedger, run_campaign, seal_manifest, validate_manifest, verify_receipt
from portable.sealed_arena_replay import ReplayRejected


def new_ledger():
    temporary_directory = tempfile.TemporaryDirectory()
    ledger = NonceLedger(Path(temporary_directory.name) / "nonce-ledger.sqlite3")
    ledger._test_tempdir = temporary_directory
    return ledger


def fixture():
    return {
        "benchmark_id": "independent-mini-v1",
        "evaluator_id": "external-evaluator-A",
        "runtime_id": "auren-runtime-test",
        "cases": [
            {"case_id": "math-001", "domain": "arithmetic", "split": "evaluation", "prompt": "What is 7 + 8?", "oracle": 15},
            {"case_id": "text-001", "domain": "classification", "split": "sealed_holdout", "prompt": "Classify: the server returned HTTP 503", "oracle": "transient"},
        ],
    }


def rehash_receipt(receipt):
    result_body = {
        key: receipt[key]
        for key in (
            "schema_version", "benchmark_id", "manifest_digest", "evaluator_id",
            "runtime_id", "policy_digest", "results", "counts", "domain_scores",
        )
    }
    receipt["result_digest"] = hashlib.sha256(
        json.dumps(result_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    body = dict(receipt)
    body.pop("receipt_digest", None)
    receipt["receipt_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    return receipt


class IndependentArenaTests(unittest.TestCase):
    def test_policy_never_receives_oracle_or_split(self):
        sealed = seal_manifest(fixture())
        observed = []

        def policy(task):
            observed.append(dict(task))
            if task["prompt"] == "What is 7 + 8?":
                return 15
            return "transient"

        receipt = run_campaign(sealed, policy, nonce_ledger=new_ledger(), policy_digest="sha256:policy-a", run_nonce="run-001")
        self.assertTrue(all(set(item) == {"prompt"} for item in observed))
        self.assertEqual(receipt["counts"], {"pass": 2, "fail": 0, "error": 0})
        self.assertTrue(verify_receipt(receipt, sealed))

    def test_result_digest_is_repeatable_but_nonce_is_bound(self):
        sealed = seal_manifest(fixture())
        policy = lambda task: 15 if task["prompt"] == "What is 7 + 8?" else "transient"
        first = run_campaign(sealed, policy, nonce_ledger=new_ledger(), policy_digest="sha256:policy-a", run_nonce="run-001")
        second = run_campaign(sealed, policy, nonce_ledger=new_ledger(), policy_digest="sha256:policy-a", run_nonce="run-002")
        self.assertEqual(first["result_digest"], second["result_digest"])
        self.assertNotEqual(first["receipt_digest"], second["receipt_digest"])
        third = run_campaign(sealed, policy, nonce_ledger=new_ledger(), policy_digest="sha256:policy-a", run_nonce="run-001")
        self.assertEqual(first["result_digest"], third["result_digest"])
        self.assertTrue(verify_receipt(first, sealed))
        self.assertTrue(verify_receipt(second, sealed))

    def test_manifest_tampering_is_rejected(self):
        sealed = seal_manifest(fixture())
        altered = copy.deepcopy(sealed)
        altered["manifest"]["cases"][0]["oracle"] = 16
        receipt = run_campaign(sealed, lambda task: 15, nonce_ledger=new_ledger(), policy_digest="p", run_nonce="n")
        self.assertFalse(verify_receipt(receipt, altered))
        with self.assertRaises(ArenaError):
            run_campaign(altered, lambda task: 15, nonce_ledger=new_ledger(), policy_digest="p", run_nonce="n")

    def test_receipt_tampering_is_rejected(self):
        sealed = seal_manifest(fixture())
        receipt = run_campaign(sealed, lambda task: 15, nonce_ledger=new_ledger(), policy_digest="p", run_nonce="n")
        receipt["counts"]["pass"] = 999
        self.assertFalse(verify_receipt(receipt, sealed))

    def test_duplicate_case_ids_and_shared_principal_fail_closed(self):
        data = fixture()
        data["cases"].append(copy.deepcopy(data["cases"][0]))
        with self.assertRaises(ArenaError):
            validate_manifest(data)
        data = fixture()
        data["runtime_id"] = data["evaluator_id"]
        with self.assertRaises(ArenaError):
            validate_manifest(data)



    def test_nonce_replay_is_rejected_across_ledger_instances(self):
        sealed = seal_manifest(fixture())
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "durable-ledger.sqlite3"
            first_ledger = NonceLedger(database)
            run_campaign(
                sealed, lambda task: 15, nonce_ledger=first_ledger,
                policy_digest="p", run_nonce="one-time-run",
            )
            reopened_ledger = NonceLedger(database)
            with self.assertRaises(ReplayRejected):
                run_campaign(
                    sealed, lambda task: 15, nonce_ledger=reopened_ledger,
                    policy_digest="p", run_nonce="one-time-run",
                )

    def test_policy_errors_are_recorded_without_exception_text(self):
        sealed = seal_manifest(fixture())

        def broken(_task):
            raise RuntimeError("secret implementation detail")

        receipt = run_campaign(sealed, broken, nonce_ledger=new_ledger(), policy_digest="p", run_nonce="n")
        self.assertEqual(receipt["counts"]["error"], 2)
        self.assertNotIn("secret implementation detail", str(receipt))
        self.assertTrue(verify_receipt(receipt, sealed))


    def test_rejects_wrong_case_lineage_even_when_digests_are_recomputed(self):
        sealed = seal_manifest(fixture())
        receipt = run_campaign(sealed, lambda task: 15 if task["prompt"] == "What is 7 + 8?" else "transient",
                               nonce_ledger=new_ledger(), policy_digest="p", run_nonce="lineage-test")
        receipt["results"][0]["case_id"] = "different-case"
        self.assertFalse(verify_receipt(rehash_receipt(receipt), sealed))

    def test_rejects_inconsistent_aggregates_even_when_digests_are_recomputed(self):
        sealed = seal_manifest(fixture())
        receipt = run_campaign(sealed, lambda task: 15 if task["prompt"] == "What is 7 + 8?" else "transient",
                               nonce_ledger=new_ledger(), policy_digest="p", run_nonce="aggregate-test")
        receipt["counts"]["pass"] = 99
        self.assertFalse(verify_receipt(rehash_receipt(receipt), sealed))
        receipt = run_campaign(sealed, lambda task: 15 if task["prompt"] == "What is 7 + 8?" else "transient",
                               nonce_ledger=new_ledger(), policy_digest="p", run_nonce="domain-test")
        receipt["domain_scores"]["arithmetic"]["passed"] = 99
        self.assertFalse(verify_receipt(rehash_receipt(receipt), sealed))

    def test_rejects_unknown_receipt_fields_even_when_digests_are_recomputed(self):
        sealed = seal_manifest(fixture())
        receipt = run_campaign(
            sealed, lambda task: 15 if task["prompt"] == "What is 7 + 8?" else "transient",
            nonce_ledger=new_ledger(), policy_digest="p", run_nonce="unknown-field-test",
        )
        receipt["promotion_authorized"] = True
        self.assertFalse(verify_receipt(rehash_receipt(receipt), sealed))

    def test_rejects_naive_timestamp_even_when_receipt_digest_is_recomputed(self):
        sealed = seal_manifest(fixture())
        receipt = run_campaign(sealed, lambda task: 15, nonce_ledger=new_ledger(),
                               policy_digest="p", run_nonce="timestamp-test")
        receipt["issued_at"] = "2026-10-11T10:00:00"
        self.assertFalse(verify_receipt(rehash_receipt(receipt), sealed))


if __name__ == "__main__":
    unittest.main()
