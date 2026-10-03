import unittest
from portable.independent_evaluation_attestation import EvaluationAttestation, IndependentEvaluationAttestor
from portable.attested_cross_project_transfer import ProjectTransferObservation, AttestedCrossProjectTransferEvaluator

class CrossProjectTransferTests(unittest.TestCase):
    def att(self):
        a=EvaluationAttestation("c","corpus","oracle","v","external","sig")
        return IndependentEvaluationAttestor(lambda x:True).verify(a,expected_campaign_digest="c",expected_corpus_digest="corpus",expected_oracle_digest="oracle")

    def rows(self,digest, n=5):
        return [ProjectTransferObservation("project-a","project-b","planner",True,False,True,True,digest) for _ in range(n)]

    def test_attested_transfer_passes(self):
        a=self.att()
        r=AttestedCrossProjectTransferEvaluator().evaluate(self.rows(a.evidence_digest),[a])
        self.assertTrue(r.trustworthy)
        self.assertEqual(r.samples,5)

    def test_regression_blocks_transfer(self):
        a=self.att()
        rows=self.rows(a.evidence_digest)
        rows[0]=ProjectTransferObservation("project-a","project-b","planner",True,True,True,True,a.evidence_digest)
        r=AttestedCrossProjectTransferEvaluator().evaluate(rows,[a])
        self.assertFalse(r.trustworthy)

    def test_unattested_observations_are_excluded(self):
        a=self.att()
        with self.assertRaises(ValueError):
            AttestedCrossProjectTransferEvaluator().evaluate(self.rows("unknown"),[a])

if __name__=="__main__":
    unittest.main()
