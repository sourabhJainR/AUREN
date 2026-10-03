import unittest

from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest


class ArenaRunReceiptTests(unittest.TestCase):
    def test_receipt_is_content_addressed(self):
        receipt = ArenaRunReceipt(
            run_id="run-1",
            arena_version="1",
            corpus_digest="corpus",
            manifest_digest="manifest",
            oracle_digest=oracle_registry_digest(("oracle-b", "oracle-a")),
            runtime_snapshot="runtime-sha",
            evaluator_version="eval-1",
            case_ids=("a", "b", "c"),
            holdout_case_ids=("c",),
            passed_case_ids=("a", "c"),
            verified_case_ids=("a", "c"),
            duration_ms=120,
            resource_measurements=(("cpu_ms", "88"), ("peak_memory_mb", "64")),
        )
        self.assertEqual(receipt.holdout_pass_rate, 1.0)
        self.assertEqual(receipt.holdout_verification_rate, 1.0)
        self.assertTrue(receipt.trustworthy)
        self.assertEqual(receipt.receipt_digest, ArenaRunReceipt(
            run_id="run-1",
            arena_version="1",
            corpus_digest="corpus",
            manifest_digest="manifest",
            oracle_digest=receipt.oracle_digest,
            runtime_snapshot="runtime-sha",
            evaluator_version="eval-1",
            case_ids=("a", "b", "c"),
            holdout_case_ids=("c",),
            passed_case_ids=("a", "c"),
            verified_case_ids=("a", "c"),
            duration_ms=120,
            resource_measurements=(("cpu_ms", "88"), ("peak_memory_mb", "64")),
        ).receipt_digest)

    def test_tampering_is_rejected(self):
        with self.assertRaises(ValueError):
            ArenaRunReceipt(
                run_id="run-1",
                arena_version="1",
                corpus_digest="corpus",
                manifest_digest="manifest",
                oracle_digest="oracle",
                runtime_snapshot="runtime-sha",
                evaluator_version="eval-1",
                case_ids=("a",),
                holdout_case_ids=(),
                passed_case_ids=("a",),
                verified_case_ids=("a",),
                duration_ms=1,
                receipt_digest="tampered",
            )

    def test_contamination_invalidates_trust(self):
        receipt = ArenaRunReceipt(
            run_id="run-2",
            arena_version="1",
            corpus_digest="corpus",
            manifest_digest="manifest",
            oracle_digest="oracle",
            runtime_snapshot="runtime-sha",
            evaluator_version="eval-1",
            case_ids=("a",),
            holdout_case_ids=("a",),
            passed_case_ids=("a",),
            verified_case_ids=("a",),
            duration_ms=1,
            contamination_detected=True,
        )
        self.assertFalse(receipt.trustworthy)

    def test_oracle_digest_is_order_independent(self):
        self.assertEqual(oracle_registry_digest(("b", "a")), oracle_registry_digest(("a", "b")))


if __name__ == "__main__":
    unittest.main()
