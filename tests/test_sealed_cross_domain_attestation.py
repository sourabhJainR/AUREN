from __future__ import annotations

import unittest
from dataclasses import replace

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    serialization = None
    Ed25519PrivateKey = None

from portable.sealed_arena_signature import encode_receipt_signature
from portable.sealed_cross_domain_arena import TargetDomainProvenance
from portable.sealed_cross_domain_attestation import (
    TargetDomainProvenanceVerifier,
    target_domain_signing_message,
)


@unittest.skipIf(Ed25519PrivateKey is None, "optional cryptography dependency is not installed")
class SignedTargetDomainProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.operator_key = Ed25519PrivateKey.generate()
        self.other_key = Ed25519PrivateKey.generate()
        self.operator_public = self.operator_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.trust = {"target-operator": self.operator_public}
        self.verifier = TargetDomainProvenanceVerifier(self.trust)
        unsigned = TargetDomainProvenance(
            domain_id="unseen-domain",
            origin="https://independent.example.org/holdouts/v1",
            manifest_digest="manifest-digest",
            split="sealed_holdout",
            development_corpus_digest="development-corpus",
            development_overlap_status="no_overlap",
            overlap_attestation_digest="signed-overlap-evidence",
            independent_operator_id="target-operator",
            attestation_digest="provenance-evidence",
            attestation_signer_id="target-operator",
        )
        signature = self.operator_key.sign(target_domain_signing_message(unsigned))
        self.provenance = replace(
            unsigned,
            attestation_signature=encode_receipt_signature("target-operator", signature),
        )

    def test_valid_independent_target_provenance_verifies(self):
        self.assertTrue(self.verifier(self.provenance))

    def test_tampered_no_overlap_claim_is_rejected(self):
        altered = replace(self.provenance, development_overlap_status="overlap")
        self.assertFalse(self.verifier(altered))

    def test_signer_must_match_named_operator(self):
        altered = replace(self.provenance, attestation_signer_id="other-operator")
        self.assertFalse(self.verifier(altered))

    def test_unknown_signer_or_malformed_signature_fails_closed(self):
        altered = replace(
            self.provenance,
            attestation_signature="unknown-operator." + self.provenance.attestation_signature.split(".", 1)[1],
        )
        self.assertFalse(self.verifier(altered))
        self.assertFalse(TargetDomainProvenanceVerifier({"other": self.operator_public})(self.provenance))


if __name__ == "__main__":
    unittest.main()
