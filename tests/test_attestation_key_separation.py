import unittest
from portable.independent_evaluation_attestation import EvaluationAttestation
from portable.attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor


class AttestationKeySeparationTests(unittest.TestCase):
    def att(self, signer="attestor-key"):
        return EvaluationAttestation("camp", "corpus", "oracle", "v1", signer, "sig")

    def policy(self):
        return AttestationTrustPolicy(frozenset({"attestor-key"}))

    @staticmethod
    def verify_identity(principal, role):
        return (role == "evaluator" and principal in {"evaluator", "same"}) or (
            role == "oracle" and principal in {"oracle-principal", "same"}
        )

    def test_distinct_trusted_signer_is_required(self):
        result = SeparationAwareAttestor(lambda a: a.signature == "sig", self.policy(), self.verify_identity).verify(
            self.att(), evaluator_id="evaluator", oracle_id="oracle-principal",
            expected_campaign_digest="camp", expected_corpus_digest="corpus", expected_oracle_digest="oracle")
        self.assertTrue(result.trustworthy)

    def test_evaluator_cannot_be_signer(self):
        result = SeparationAwareAttestor(lambda _a: True, self.policy(), self.verify_identity).verify(
            self.att("evaluator"), evaluator_id="evaluator", oracle_id="oracle-principal",
            expected_campaign_digest="camp", expected_corpus_digest="corpus", expected_oracle_digest="oracle")
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("signer" in reason for reason in result.reasons))

    def test_oracle_cannot_be_evaluator(self):
        result = SeparationAwareAttestor(lambda _a: True, self.policy(), self.verify_identity).verify(
            self.att(), evaluator_id="same", oracle_id="same",
            expected_campaign_digest="camp", expected_corpus_digest="corpus", expected_oracle_digest="oracle")
        self.assertFalse(result.trustworthy)

    def test_unverified_principal_identity_fails_closed(self):
        verifier = SeparationAwareAttestor(
            lambda _a: True, self.policy(), lambda _principal, _role: False
        )
        result = verifier.verify(
            self.att(), evaluator_id="evaluator", oracle_id="oracle-principal",
            expected_campaign_digest="camp", expected_corpus_digest="corpus", expected_oracle_digest="oracle")
        self.assertFalse(result.independent)
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("identity is not authenticated" in reason for reason in result.reasons))

    def test_unknown_signer_fails_closed(self):
        result = SeparationAwareAttestor(lambda _a: True, self.policy(), self.verify_identity).verify(
            self.att("unknown"), evaluator_id="evaluator", oracle_id="oracle-principal",
            expected_campaign_digest="camp", expected_corpus_digest="corpus", expected_oracle_digest="oracle")
        self.assertFalse(result.trustworthy)

if __name__ == "__main__":
    unittest.main()
