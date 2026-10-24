from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from portable.sealed_arena_boundary import (
    ExternalOutcomeReceipt,
    SealedArenaBoundary,
    SealedCampaignRequest,
    SealedCaseEnvelope,
)
from portable.sealed_corpus_provenance import SealedCorpusProvenance
from portable.sealed_arena_replay import ReplayRejected, SealedArenaReplayLedger


class SealedArenaBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True, ("novel-domain",))
        base = SealedCampaignRequest("campaign-d", "corpus-d", (self.case,), "nonce-1")
        provenance = SealedCorpusProvenance(
            corpus_digest="corpus-d", manifest_digest=base.manifest_digest,
            origin="https://benchmarks.example.org/holdout/v1", split="sealed_holdout",
            contamination_status="clean", independent_operator_id="operator",
            attestation_digest="attestation-d",
        )
        self.request = replace(base, provenance=provenance)

    def receipt(self, **overrides):
        values = {
            "campaign_digest": "campaign-d",
            "corpus_digest": "corpus-d",
            "evaluator_id": "evaluator",
            "oracle_id": "oracle",
            "signature": "sig",
            "results": (("case-1", True, "evidence-d"),),
            "contamination_free": True,
            "independent_oracle": True,
            "run_nonce": "nonce-1",
            "request_digest": self.request.request_digest,
        }
        values.update(overrides)
        return ExternalOutcomeReceipt(**values)

    def test_accepts_opaque_independent_receipt(self) -> None:
        evidence = SealedArenaBoundary(lambda value: value.signature == "sig").accept(
            self.request, self.receipt()
        )
        self.assertEqual(evidence.holdout_pass_rate, 1.0)
        self.assertEqual(evidence.result_case_count, 1)

    def test_boundary_rejects_unknown_contamination_provenance(self) -> None:
        contaminated = replace(self.request, provenance=replace(self.request.provenance, contamination_status="unknown"))
        receipt = self.receipt(request_digest=contaminated.request_digest)
        with self.assertRaisesRegex(ValueError, "rejects contaminated"):
            SealedArenaBoundary(lambda _value: True).accept(contaminated, receipt)

    def test_rejects_missing_corpus_provenance(self) -> None:
        request = SealedCampaignRequest("campaign-d", "corpus-d", (self.case,), "nonce-1")
        receipt = self.receipt(request_digest=request.request_digest)
        with self.assertRaisesRegex(ValueError, "missing corpus provenance"):
            SealedArenaBoundary(lambda _value: True).accept(request, receipt)


    def test_rejects_expired_receipt(self) -> None:
        expired = self.receipt(issued_at_epoch=100.0, expires_at_epoch=199.0)
        with self.assertRaisesRegex(ValueError, "stale or expired"):
            SealedArenaBoundary(lambda _value: True, clock=lambda: 200.0).accept(self.request, expired)

    def test_rejects_receipt_issued_too_far_in_future(self) -> None:
        future = self.receipt(issued_at_epoch=250.0, expires_at_epoch=400.0)
        with self.assertRaisesRegex(ValueError, "issued in the future"):
            SealedArenaBoundary(lambda _value: True, clock=lambda: 200.0, max_future_skew_seconds=10.0).accept(self.request, future)

    def test_accepts_fresh_receipt_with_injected_clock(self) -> None:
        fresh = self.receipt(issued_at_epoch=190.0, expires_at_epoch=220.0)
        evidence = SealedArenaBoundary(lambda _value: True, clock=lambda: 200.0).accept(self.request, fresh)
        self.assertEqual(evidence.holdout_pass_rate, 1.0)

    def test_rejects_missing_or_blank_run_nonce(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_nonce"):
            SealedCampaignRequest("campaign-d", "corpus-d", (self.case,))
        with self.assertRaisesRegex(ValueError, "run_nonce"):
            self.receipt(run_nonce=" ")

    def test_rejects_non_boolean_holdout(self) -> None:
        for value in ("false", 0, 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "holdout must be a boolean"):
                SealedCaseEnvelope("case-x", "task-d", "input-d", "env-d", value)

    def test_rejects_non_boolean_trust_flags(self) -> None:
        for field in ("contamination_free", "independent_oracle"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"{field} must be a boolean"):
                self.receipt(**{field: "false"})

    def test_rejects_benchmark_answer_material(self) -> None:
        with self.assertRaises(ValueError):
            self.request.validate_task_metadata({"prompt": "opaque", "answer": "gold"})

    def test_rejects_known_answer_aliases_nested_in_metadata(self) -> None:
        for key in (
            "ground_truth", "correct-answer", "reference answer", "answer_key",
            "gold_label", "expected_output", "oracle_output", "gold_output",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "answer material"):
                self.request.validate_task_metadata({"task": {"parameters": [{key: "secret"}]}})

    def test_rejects_answer_keys_with_camel_case_and_punctuation(self) -> None:
        for key in (
            "answerKey", "oracleAnswer", "groundTruth", "expectedOutput",
            "Correct.Answer", "gold-label", "referenceAnswer",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "answer material"):
                self.request.validate_task_metadata({"task": {"metadata": {key: "secret"}}})

    def test_allows_non_answer_metadata_keys(self) -> None:
        self.request.validate_task_metadata({
            "prompt_digest": "opaque",
            "task_name": "benchmark-case",
            "answer_format": "json",
            "answer_budget": 3,
        })

    def test_rejects_answer_keys_with_camel_case_and_punctuation(self) -> None:
        for key in (
            "answerKey", "oracleAnswer", "groundTruth", "expectedOutput",
            "Correct.Answer", "gold-label", "referenceAnswer",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "answer material"):
                self.request.validate_task_metadata({"task": {"metadata": {key: "secret"}}})

    def test_allows_non_answer_metadata_keys(self) -> None:
        self.request.validate_task_metadata({
            "prompt_digest": "opaque",
            "task_name": "benchmark-case",
            "answer_format": "json",
            "answer_budget": 3,
        })

    def test_rejects_non_independent_principals(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct principals"):
            self.receipt(evaluator_id="same", oracle_id="same")

    def test_missing_trust_claims_default_to_fail_closed(self) -> None:
        receipt = ExternalOutcomeReceipt(
            campaign_digest="campaign-d",
            corpus_digest="corpus-d",
            evaluator_id="evaluator",
            oracle_id="oracle",
            signature="sig",
            results=(("case-1", True, "evidence-d"),),
            run_nonce="nonce-1",
            request_digest=self.request.request_digest,
        )
        self.assertFalse(receipt.contamination_free)
        self.assertFalse(receipt.independent_oracle)
        with self.assertRaisesRegex(ValueError, "rejects contaminated"):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)

    def test_rejects_contamination(self) -> None:
        receipt = self.receipt(contamination_free=False)
        with self.assertRaisesRegex(ValueError, "rejects contaminated"):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)

    def test_rejects_non_independent_oracle_flag(self) -> None:
        receipt = self.receipt(independent_oracle=False)
        with self.assertRaisesRegex(ValueError, "rejects contaminated"):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)

    def test_rejects_partial_campaign_receipt(self) -> None:
        second = SealedCaseEnvelope("case-2", "task-2", "input-2", "env-2", False)
        base = SealedCampaignRequest("campaign-d", "corpus-d", (self.case, second), "nonce-1")
        provenance = SealedCorpusProvenance(
            corpus_digest="corpus-d", manifest_digest=base.manifest_digest,
            origin="https://benchmarks.example.org/holdout/v1", split="sealed_holdout",
            contamination_status="clean", independent_operator_id="operator",
            attestation_digest="attestation-d",
        )
        request = replace(base, provenance=provenance)
        receipt = self.receipt(results=(("case-1", True, "evidence-d"),), request_digest=request.request_digest)
        with self.assertRaisesRegex(ValueError, "incomplete") as caught:
            SealedArenaBoundary(lambda _value: True).accept(request, receipt)
        self.assertNotIn("case-2", str(caught.exception))
        self.assertIn("missing case count: 1", str(caught.exception))

    def test_rejects_non_boolean_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be booleans"):
            self.receipt(results=(("case-1", "false", "evidence-d"),))

    def test_rejects_nested_answer_material(self) -> None:
        with self.assertRaisesRegex(ValueError, "answer material"):
            self.request.validate_task_metadata({"task": {"parameters": [{"expected-answer": "gold"}]}})

    def test_rejects_unknown_case(self) -> None:
        receipt = self.receipt(results=(("case-unknown", True, "evidence-d"),))
        with self.assertRaisesRegex(ValueError, "unknown case"):
            SealedArenaBoundary(lambda _value: True).accept(self.request, receipt)

    def test_rejects_signature_failure(self) -> None:
        with self.assertRaisesRegex(ValueError, "signature is not verified"):
            SealedArenaBoundary(lambda _value: False).accept(self.request, self.receipt())

    def test_rejects_receipt_for_modified_case_set(self) -> None:
        altered_case = SealedCaseEnvelope("case-1", "task-d", "changed-input", "env-d", True, ("novel-domain",))
        altered_base = SealedCampaignRequest("campaign-d", "corpus-d", (altered_case,), "nonce-1")
        altered_provenance = SealedCorpusProvenance(
            corpus_digest="corpus-d", manifest_digest=altered_base.manifest_digest,
            origin="https://benchmarks.example.org/holdout/v1", split="sealed_holdout",
            contamination_status="clean", independent_operator_id="operator",
            attestation_digest="attestation-d",
        )
        altered_request = replace(altered_base, provenance=altered_provenance)
        with self.assertRaisesRegex(ValueError, "request digest"):
            SealedArenaBoundary(lambda value: value.signature == "sig").accept(
                altered_request, self.receipt()
            )

    def test_rejects_nonce_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonce"):
            SealedArenaBoundary(lambda _value: True).accept(
                self.request, self.receipt(run_nonce="other-nonce")
            )

    def test_persistent_ledger_rejects_replay_after_boundary_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "sealed-replay.sqlite3"
            first_ledger = SealedArenaReplayLedger(ledger_path)
            first = SealedArenaBoundary(
                lambda value: value.signature == "sig",
                replay_ledger=first_ledger,
            )
            accepted = first.accept(self.request, self.receipt())
            self.assertEqual(accepted.request_digest, self.request.request_digest)

            # A newly constructed boundary simulates process restart. The replay
            # decision must come from durable operator-owned state, not RAM.
            second = SealedArenaBoundary(
                lambda value: value.signature == "sig",
                replay_ledger=SealedArenaReplayLedger(ledger_path),
            )
            with self.assertRaisesRegex(ReplayRejected, "already been used"):
                second.accept(self.request, self.receipt())


if __name__ == "__main__":
    unittest.main()
