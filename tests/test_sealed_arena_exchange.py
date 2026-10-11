from __future__ import annotations

import hashlib
import json
import unittest

from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_exchange import export_campaign_request, export_outcome_receipt, import_campaign_request, import_outcome_receipt


class SealedArenaExchangeTests(unittest.TestCase):
    def setUp(self) -> None:
        case = SealedCaseEnvelope("case-1", "task-d", "input-d", "env-d", True)
        self.request = SealedCampaignRequest("campaign-d", "corpus-d", (case,), "nonce-1")
        self.receipt = ExternalOutcomeReceipt(
            "campaign-d", "corpus-d", "evaluator", "oracle", "sig",
            (("case-1", True, "evidence-d"),), run_nonce="nonce-1",
            request_digest=self.request.request_digest,
            contamination_free=True, independent_oracle=True,
            metrics=(("case-1", 0.9, 0.8, 0.1, 12.5, True),),
        )

    def test_request_round_trip(self) -> None:
        round_trip = import_campaign_request(export_campaign_request(self.request))
        self.assertEqual(round_trip.campaign_digest, self.request.campaign_digest)
        self.assertEqual(round_trip.corpus_digest, self.request.corpus_digest)
        self.assertEqual(round_trip.run_nonce, self.request.run_nonce)
        self.assertEqual(round_trip.request_digest, self.request.request_digest)
        self.assertEqual(round_trip.cases[0].transfer_dimensions, self.request.cases[0].transfer_dimensions)

    def test_receipt_round_trip(self) -> None:
        self.assertEqual(import_outcome_receipt(export_outcome_receipt(self.receipt)), self.receipt)

    def test_tampering_fails_closed(self) -> None:
        raw = export_campaign_request(self.request).replace(b"corpus-d", b"corpus-x")
        with self.assertRaises(ValueError):
            import_campaign_request(raw)

    def test_exchange_does_not_contain_answer_fields(self) -> None:
        raw = export_campaign_request(self.request).decode("utf-8").lower()
        for token in ("answer", "expected_answer", "oracle_answer", "gold", "solution"):
            self.assertNotIn(token, raw)

    def test_case_tampering_fails_even_when_outer_digest_is_recomputed(self) -> None:
        body = json.loads(export_campaign_request(self.request).decode("utf-8"))
        body["payload"]["cases"][0]["input_digest"] = "tampered-input"
        body["payload_digest"] = hashlib.sha256(
            json.dumps(body["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "request digest mismatch"):
            import_campaign_request(raw)

    def test_missing_nonce_in_exchange_fails_closed(self) -> None:
        body = json.loads(export_campaign_request(self.request).decode("utf-8"))
        del body["payload"]["run_nonce"]
        body["payload_digest"] = hashlib.sha256(
            json.dumps(body["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "run_nonce"):
            import_campaign_request(raw)


if __name__ == "__main__":
    unittest.main()
