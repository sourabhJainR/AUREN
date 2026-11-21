import unittest

from portable.arena_run_receipt import ArenaRunReceipt, attach_rubric_receipt, oracle_registry_digest
from portable.evaluation_rubric import RubricCriterion, RubricDefinition, evaluate_rubric


class ArenaRunReceiptTests(unittest.TestCase):
    def make_receipt(self, **overrides):
        values = dict(
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
        values.update(overrides)
        return ArenaRunReceipt(**values)

    def test_receipt_is_content_addressed(self):
        receipt = self.make_receipt()
        duplicate = self.make_receipt()
        self.assertEqual(receipt.holdout_pass_rate, 1.0)
        self.assertEqual(receipt.holdout_verification_rate, 1.0)
        self.assertTrue(receipt.trustworthy)
        self.assertEqual(receipt.receipt_digest, duplicate.receipt_digest)


    def test_arena_receipt_binds_versioned_rubric_and_blocks_nonpassing_score(self):
        rubric = RubricDefinition(
            "arena-quality", "1.0.0",
            (
                RubricCriterion("correctness", "Correctness", 50, ("tests",), True),
                RubricCriterion("evidence", "Evidence", 50, ("report",)),
            ),
            hard_gate_ids=("guard",),
        )
        def score(gate):
            return evaluate_rubric(
                rubric, subject_id="arena-candidate", run_id="run-1",
                source_digest="a" * 64, plan_digest="b" * 64,
                task_ids=("arena-validation",),
                artifact_digests=("c" * 64,), evaluator_id="independent-oracle",
                evaluator_version="oracle-v1", executor_id="candidate",
                scores={"correctness": 100, "evidence": 100},
                evidence={"correctness": ["tests://1"], "evidence": ["report://1"]},
                rationales={"correctness": "Tests passed", "evidence": "Report verified"},
                hard_gates={"guard": gate},
            )
        base = self.make_receipt()
        passed = attach_rubric_receipt(base, score(True))
        self.assertEqual(passed.rubric_digest, rubric.digest)
        self.assertEqual(passed.rubric_status, "passed")
        self.assertEqual(passed.executor_id, "candidate")
        self.assertEqual(passed.evaluator_id, "independent-oracle")
        self.assertTrue(passed.trustworthy)
        blocked = attach_rubric_receipt(base, score(False))
        self.assertEqual(blocked.rubric_status, "blocked")
        self.assertFalse(blocked.trustworthy)
        self.assertNotEqual(base.receipt_digest, passed.receipt_digest)

    def test_arena_rubric_binding_rejects_wrong_run(self):
        rubric = RubricDefinition(
            "arena-quality", "1.0.0",
            (RubricCriterion("correctness", "Correctness", 100, ("tests",)),),
        )
        receipt = evaluate_rubric(
            rubric, subject_id="candidate", run_id="other-run",
            source_digest="a" * 64, plan_digest="b" * 64,
            task_ids=("arena-validation",),
            artifact_digests=("c" * 64,), executor_id="candidate-executor",
            evaluator_id="qa", evaluator_version="qa-v1", scores={"correctness": 100},
            evidence={"correctness": ["tests://1"]},
            rationales={"correctness": "Passed"}, hard_gates={},
        )
        with self.assertRaises(ValueError):
            attach_rubric_receipt(self.make_receipt(), receipt)

    def test_campaign_binding_changes_receipt_digest(self):
        unbound = self.make_receipt()
        bound = self.make_receipt(campaign_digest="campaign-digest")
        self.assertNotEqual(unbound.receipt_digest, bound.receipt_digest)
        self.assertEqual(bound.as_dict()["campaign_digest"], "campaign-digest")

    def test_tampering_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "receipt_digest"):
            self.make_receipt(receipt_digest="tampered")

    def test_contamination_invalidates_trust(self):
        receipt = self.make_receipt(run_id="run-2", case_ids=("a",), holdout_case_ids=("a",),
                                    passed_case_ids=("a",), verified_case_ids=("a",), duration_ms=1,
                                    contamination_detected=True)
        self.assertFalse(receipt.trustworthy)

    def test_ambiguous_contamination_flag_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contamination_detected must be a boolean"):
            self.make_receipt(contamination_detected="false")

    def test_oracle_digest_is_order_independent(self):
        self.assertEqual(oracle_registry_digest(("b", "a")), oracle_registry_digest(("a", "b")))


if __name__ == "__main__":
    unittest.main()
