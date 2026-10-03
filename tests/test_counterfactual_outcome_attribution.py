import unittest
from portable.counterfactual_outcome_attribution import (
    CounterfactualObservation, CounterfactualOutcomeAttributor,
)

class CounterfactualAttributionTests(unittest.TestCase):
    def test_paired_independent_outcomes_produce_lift(self):
        rows=[]
        for i in range(4):
            rows += [
                CounterfactualObservation("e1",f"case-{i}","treatment",.9,10,1,True),
                CounterfactualObservation("e1",f"case-{i}","control",.7,11,1,True),
            ]
        result=CounterfactualOutcomeAttributor().evaluate("e1","treatment","control",rows)
        self.assertTrue(result.trustworthy)
        self.assertAlmostEqual(result.quality_lift,.2)
        self.assertAlmostEqual(result.treatment_pass_rate,1)
        self.assertAlmostEqual(result.control_pass_rate,1)

    def test_unpaired_or_untrusted_results_fail_closed(self):
        rows=[
            CounterfactualObservation("e1","case-1","treatment",1,1,1,True),
            CounterfactualObservation("e1","case-1","control",1,1,1,True,contaminated=True),
        ]
        with self.assertRaises(ValueError):
            CounterfactualOutcomeAttributor().evaluate("e1","treatment","control",rows)

    def test_digest_is_deterministic(self):
        rows=[
            CounterfactualObservation("e1","case-1","treatment",.8,1,1,True),
            CounterfactualObservation("e1","case-1","control",.6,1,1,True),
        ]
        a=CounterfactualOutcomeAttributor()
        self.assertEqual(a.evaluate("e1","treatment","control",rows).evidence_digest,
                         a.evaluate("e1","treatment","control",rows).evidence_digest)

if __name__=="__main__":
    unittest.main()
