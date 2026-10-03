import unittest
from portable.independent_evaluation_attestation import EvaluationAttestation, IndependentEvaluationAttestor

class AttestationTests(unittest.TestCase):
    def att(self):
        return EvaluationAttestation("camp","corpus","oracle","v1","external-evaluator","sig")

    def test_matching_verified_attestation_is_trustworthy(self):
        r=IndependentEvaluationAttestor(lambda a:a.signature=="sig").verify(
            self.att(),expected_campaign_digest="camp",expected_corpus_digest="corpus",expected_oracle_digest="oracle")
        self.assertTrue(r.trustworthy)

    def test_wrong_lineage_or_signature_fails_closed(self):
        r=IndependentEvaluationAttestor(lambda a:False).verify(
            self.att(),expected_campaign_digest="other",expected_corpus_digest="corpus",expected_oracle_digest="oracle")
        self.assertFalse(r.trustworthy)
        self.assertTrue(r.reasons)

    def test_contamination_blocks_trust(self):
        r=IndependentEvaluationAttestor(lambda a:True).verify(
            self.att(),expected_campaign_digest="camp",expected_corpus_digest="corpus",expected_oracle_digest="oracle",
            contamination_detected=True)
        self.assertFalse(r.trustworthy)

if __name__=="__main__":
    unittest.main()
