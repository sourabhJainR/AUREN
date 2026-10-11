import unittest

from portable.independent_evaluation_attestation import EvaluationAttestation, IndependentEvaluationAttestor
from portable.attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor


class AttestationTests(unittest.TestCase):
    def att(self):
        return EvaluationAttestation("camp", "corpus", "oracle", "v1", "external-evaluator", "sig")

    def test_signature_and_lineage_alone_do_not_establish_independence(self):
        result = IndependentEvaluationAttestor(lambda a: a.signature == "sig").verify(
            self.att(),
            expected_campaign_digest="camp",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
        )
        self.assertTrue(result.verified)
        self.assertFalse(result.independent)
        self.assertFalse(result.trustworthy)

    def test_matching_verified_attestation_is_trustworthy_after_separation(self):
        verifier = SeparationAwareAttestor(
            lambda a: a.signature == "sig",
            AttestationTrustPolicy(frozenset({"external-evaluator"})),
        )
        result = verifier.verify(
            self.att(),
            evaluator_id="runtime-evaluator",
            oracle_id="oracle-service",
            expected_campaign_digest="camp",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
        )
        self.assertTrue(result.trustworthy)
        self.assertTrue(result.independent)

    def test_wrong_lineage_or_signature_fails_closed(self):
        result = IndependentEvaluationAttestor(lambda _a: False).verify(
            self.att(),
            expected_campaign_digest="other",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
        )
        self.assertFalse(result.trustworthy)
        self.assertTrue(result.reasons)

    def test_contamination_blocks_trust_after_separation(self):
        verifier = SeparationAwareAttestor(
            lambda _a: True,
            AttestationTrustPolicy(frozenset({"external-evaluator"})),
        )
        result = verifier.verify(
            self.att(),
            evaluator_id="runtime-evaluator",
            oracle_id="oracle-service",
            expected_campaign_digest="camp",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
            contamination_detected=True,
        )
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("contamination" in reason for reason in result.reasons))

    def test_separation_context_is_bound_into_evidence_digest(self):
        verifier = SeparationAwareAttestor(
            lambda _a: True,
            AttestationTrustPolicy(frozenset({"external-evaluator"})),
        )
        kwargs = dict(
            oracle_id="oracle-service",
            expected_campaign_digest="camp",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
        )
        first = verifier.verify(self.att(), evaluator_id="runtime-A", **kwargs)
        second = verifier.verify(self.att(), evaluator_id="runtime-B", **kwargs)
        self.assertTrue(first.trustworthy)
        self.assertTrue(second.trustworthy)
        self.assertNotEqual(first.evidence_digest, second.evidence_digest)

    def test_attestation_identifiers_must_be_actual_strings(self):
        with self.assertRaisesRegex(ValueError, "signer_id"):
            EvaluationAttestation("camp", "corpus", "oracle", "v1", None, "sig")


if __name__ == "__main__":
    unittest.main()
