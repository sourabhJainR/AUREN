from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # optional dependency; the dedicated CI job installs it
    serialization = None
    Ed25519PrivateKey = None

from portable.sealed_arena_boundary import ExternalOutcomeReceipt
from portable.sealed_arena_replay import SealedArenaReplayLedger
from portable.sealed_arena_boundary import SealedArenaBoundary
from portable.sealed_arena_signature import (
    Ed25519ReceiptVerifier, encode_receipt_signature, receipt_signing_message,
)


@unittest.skipIf(Ed25519PrivateKey is None, "optional cryptography dependency is not installed")
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
                receipt = replace(
                    self.receipt,
                    evaluator_id=principal if principal == "evaluator-1" else "evaluator-1",
                    oracle_id=principal if principal == "oracle-1" else "oracle-1",
                )
                signature = self.private.sign(receipt_signing_message(receipt, principal))
                signed = replace(receipt, signature=encode_receipt_signature(principal, signature))
                verifier = Ed25519ReceiptVerifier({principal: self.public})
                self.assertFalse(verifier(signed))

    def test_empty_or_duplicate_trust_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            Ed25519ReceiptVerifier({})
        with self.assertRaisesRegex(ValueError, "multiple trusted signer IDs"):
            Ed25519ReceiptVerifier({"lab-a": self.public, "lab-b": self.public})

    def test_revoked_key_cannot_authenticate_old_or_new_receipts(self):
        signed = self.signed()
        revoked = Ed25519ReceiptVerifier(
            {"independent-lab": self.public}, revoked_signers={"independent-lab"}
        )
        self.assertFalse(revoked(signed))
        self.assertFalse(revoked.verify_detached("independent-lab", signed.signature.split(".", 1)[1], b"message"))

    def test_key_validity_window_is_enforced_with_injected_clock(self):
        signed = self.signed()
        before = Ed25519ReceiptVerifier(
            {"independent-lab": self.public},
            key_validity={"independent-lab": (100.0, 200.0)},
            clock=lambda: 99.0,
        )
        active = Ed25519ReceiptVerifier(
            {"independent-lab": self.public},
            key_validity={"independent-lab": (100.0, 200.0)},
            clock=lambda: 150.0,
        )
        expired = Ed25519ReceiptVerifier(
            {"independent-lab": self.public},
            key_validity={"independent-lab": (100.0, 200.0)},
            clock=lambda: 200.0,
        )
        self.assertFalse(before(signed))
        self.assertTrue(active(signed))
        self.assertFalse(expired(signed))

    def test_key_lifecycle_policy_rejects_unknown_ids_and_invalid_windows(self):
        with self.assertRaisesRegex(ValueError, "exist in the trusted key map"):
            Ed25519ReceiptVerifier({"lab": self.public}, revoked_signers={"missing-lab"})
        with self.assertRaisesRegex(ValueError, "only be configured for trusted signers"):
            Ed25519ReceiptVerifier({"lab": self.public}, key_validity={"missing-lab": (1.0, 2.0)})
        with self.assertRaisesRegex(ValueError, "later than not_before"):
            Ed25519ReceiptVerifier({"lab": self.public}, key_validity={"lab": (5.0, 5.0)})
        with self.assertRaisesRegex(ValueError, "collection"):
            Ed25519ReceiptVerifier({"lab": self.public}, revoked_signers="lab")

    def test_production_boundary_enforces_revocation_and_key_validity_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = SealedArenaReplayLedger(Path(directory) / "replay.sqlite3")
            revoked = SealedArenaBoundary.from_trusted_ed25519_keys(
                {"independent-lab": self.public},
                replay_ledger=ledger,
                revoked_signers={"independent-lab"},
            )
            self.assertFalse(revoked._verify_signature(self.signed()))

            not_yet_valid = SealedArenaBoundary.from_trusted_ed25519_keys(
                {"independent-lab": self.public},
                replay_ledger=ledger,
                key_validity={"independent-lab": (200.0, 300.0)},
                clock=lambda: 199.0,
            )
            self.assertFalse(not_yet_valid._verify_signature(self.signed()))
            active = SealedArenaBoundary.from_trusted_ed25519_keys(
                {"independent-lab": self.public},
                replay_ledger=ledger,
                key_validity={"independent-lab": (100.0, 300.0)},
                clock=lambda: 199.0,
            )
            self.assertTrue(active._verify_signature(self.signed()))


if __name__ == "__main__":
    unittest.main()
