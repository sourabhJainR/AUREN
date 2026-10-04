from __future__ import annotations

import unittest

from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope


class SealedArenaBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True, ("novel-domain",))
        self.request = SealedCampaignRequest("campaign-d", "corpus-d", (self.case,))

    def test_accepts_opaque_independent_receipt(self) -> None:
        receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),))
        evidence = SealedArenaBoundary(lambda value: value.signature == "sig").accept(self.request, receipt)
        self.assertEqual(evidence.holdout_pass_rate, 1.0)
        self.assertEqual(evidence.verified_case_count, 1)

    def test_rejects_benchmark_answer_material(self) -> None:
        with self.assertRaises(ValueError):
            self.request.validate_task_metadata({"prompt": "opaque", "answer": "gold"})

    def test_rejects_non_independent_principals(self) -> None:
        with self.assertRaises(ValueError):
            ExternalOutcomeReceipt("campaign-d", "corpus-d", "same", "same", "sig", (("case-1", True, "evidence-d"),))

    def test_rejects_contamination(self) -> None:
        receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-1", True, "evidence-d"),), contamination_free=False)
        with self.assertRaises(ValueError):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)

    def test_rejects_unknown_case(self) -> None:
        receipt = ExternalOutcomeReceipt("campaign-d", "corpus-d", "evaluator", "oracle", "sig", (("case-unknown", True, "evidence-d"),))
        with self.assertRaises(ValueError):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)


if __name__ == "__main__":
    unittest.main()
