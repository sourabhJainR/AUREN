from __future__ import annotations

import unittest
from dataclasses import replace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from portable.sealed_arena_boundary import ExternalOutcomeReceipt
from portable.sealed_arena_signature import (
    Ed25519ReceiptVerifier, encode_receipt_signature, receipt_signing_message,
)


class Ed25519ReceiptVerifierTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.verifier = Ed25519ReceiptVerifier({"independent-lab": self.public})
        self.receipt = ExternalOutcomeReceipt(
            campaign_digest="campaign-1", corpus_digest="corpus-1",
            evaluator_id="evaluator-1", oracle_id="oracle-1",
            signature="placeholder",
            results=(("case-1", True, "evidence-1"),),
            contamination_free=True, independent_oracle=True,
            run_nonce="nonce-1", request_digest="request-1",
        )

    def signed(self, receipt=None, signer="independent-lab"):
        receipt = receipt or self.receipt
        signature = self.private.sign(receipt_signing_message(receipt, signer))
        return replace(receipt, signature=encode_receipt_signature(signer, signature))

    def test_valid_signature_authenticates_exact_receipt(self):
        self.assertTrue(self.verifier(self.signed()))

    def test_result_tampering_invalidates_signature(self):
        signed = self.signed()
        changed = replace(signed, results=(("case-1", False, "evidence-1"),))
        self.assertFalse(self.verifier(changed))

    def test_freshness_and_nonce_are_signature_bound(self):
        signed = self.signed()
        self.assertFalse(self.verifier(replace(signed, run_nonce="replayed-nonce")))
        self.assertFalse(self.verifier(replace(signed, expires_at_epoch=signed.expires_at_epoch + 1)))

    def test_unknown_signer_and_malformed_signatures_fail_closed(self):
        signed = self.signed()
        self.assertFalse(Ed25519ReceiptVerifier({"other-lab": self.public})(signed))
        self.assertFalse(self.verifier(replace(signed, signature="unknown.abc")))
        self.assertFalse(self.verifier(replace(signed, signature="independent-lab.abc")))

    def test_evaluator_or_oracle_cannot_be_signer(self):
        for principal in ("evaluator-1", "oracle-1"):
            with self.subTest(principal=principal):
                receipt = replace(self.receipt, evaluator_id=principal, oracle_id="other")
                signature = self.private.sign(receipt_signing_message(receipt, principal))
                signed = replace(receipt, signature=encode_receipt_signature(principal, signature))
                verifier = Ed25519ReceiptVerifier({principal: self.public})
                self.assertFalse(verifier(signed))

    def test_empty_or_duplicate_trust_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            Ed25519ReceiptVerifier({})
        with self.assertRaisesRegex(ValueError, "multiple trusted signer IDs"):
            Ed25519ReceiptVerifier({"lab-a": self.public, "lab-b": self.public})


if __name__ == "__main__":
    unittest.main()
