from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    serialization = None
    Ed25519PrivateKey = None

from portable.sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_custody import provenance_signing_message
from portable.sealed_arena_signature import encode_receipt_signature, receipt_signing_message
from portable.sealed_corpus_provenance import SealedCorpusProvenance


@unittest.skipIf(Ed25519PrivateKey is None, "optional cryptography dependency is not installed")
class SignedHoldoutCustodyTests(unittest.TestCase):
    def setUp(self):
        self.operator_key = Ed25519PrivateKey.generate()
        self.evaluator_key = Ed25519PrivateKey.generate()
        self.operator_public = self.operator_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.evaluator_public = self.evaluator_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.trust = {"independent-operator": self.operator_public, "external-evaluator": self.evaluator_public}
        case = SealedCaseEnvelope("case-1", "task-d", "input-d", "environment-d", True)
        base = SealedCampaignRequest("campaign-1", "corpus-1", (case,), "nonce-1")
        unsigned = SealedCorpusProvenance(
            corpus_digest="corpus-1",
            manifest_digest=base.manifest_digest,
            origin="https://independent.example.org/holdouts/v1",
            split="sealed_holdout",
            contamination_status="clean",
            independent_operator_id="independent-operator",
            attestation_digest=hashlib.sha256(b"custody-record-v1").hexdigest(),
        )
        signature = self.operator_key.sign(provenance_signing_message(unsigned))
        provenance = replace(
            unsigned,
            attestation_signer_id="independent-operator",
            attestation_signature=encode_receipt_signature("independent-operator", signature).split(".", 1)[1],
        )
        self.request = replace(base, provenance=provenance)

    def make_receipt(self, request=None, *, signer="external-evaluator"):
        request = request or self.request
        unsigned = ExternalOutcomeReceipt(
            campaign_digest=request.campaign_digest,
            corpus_digest=request.corpus_digest,
            evaluator_id="evaluator-service",
            oracle_id="oracle-service",
            signature="placeholder",
            results=(("case-1", True, "evidence-digest"),),
            contamination_free=True,
            independent_oracle=True,
            run_nonce=request.run_nonce,
            request_digest=request.request_digest,
        )
        key = self.evaluator_key if signer == "external-evaluator" else self.operator_key
        signature = key.sign(receipt_signing_message(unsigned, signer))
        return replace(unsigned, signature=encode_receipt_signature(signer, signature))

    def boundary(self):
        return SealedArenaBoundary.from_trusted_ed25519_keys(self.trust)

    def test_boundary_requires_both_operator_custody_and_evaluator_signatures(self):
        evidence = self.boundary().accept(self.request, self.make_receipt())
        self.assertEqual(evidence.holdout_pass_rate, 1.0)

    def test_unsigned_provenance_is_rejected_by_trusted_boundary(self):
        unsigned = replace(
            self.request.provenance, attestation_signer_id="", attestation_signature=""
        )
        request = replace(self.request, provenance=unsigned)
        with self.assertRaisesRegex(ValueError, "missing its independent operator signature"):
            self.boundary().accept(request, self.make_receipt(request))

    def test_tampered_custody_metadata_is_rejected_even_with_fresh_outcome_signature(self):
        tampered_provenance = replace(self.request.provenance, origin="https://attacker.example.org/holdouts")
        tampered_request = replace(self.request, provenance=tampered_provenance)
        with self.assertRaisesRegex(ValueError, "custody signature is not verified"):
            self.boundary().accept(tampered_request, self.make_receipt(tampered_request))

    def test_wrong_custody_signer_is_rejected(self):
        provenance = replace(self.request.provenance, attestation_signer_id="external-evaluator")
        request = replace(self.request, provenance=provenance)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.boundary().accept(request, self.make_receipt(request))

    def test_outcome_signer_cannot_be_the_named_evaluator_or_oracle(self):
        receipt = self.make_receipt(signer="external-evaluator")
        receipt = replace(receipt, evaluator_id="external-evaluator")
        with self.assertRaisesRegex(ValueError, "signature is not verified"):
            self.boundary().accept(self.request, receipt)


if __name__ == "__main__":
    unittest.main()
