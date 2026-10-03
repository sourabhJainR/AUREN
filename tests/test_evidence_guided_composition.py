import unittest
from portable.open_ended_capability_discovery import CapabilityGap
from portable.evidence_guided_composition import EvidenceGuidedCompositionPlanner

class CompositionPlannerTests(unittest.TestCase):
    def gap(self):
        return CapabilityGap("g1",("e1","e2"),("domain-a","domain-b"),("long-horizon","adversarial"),.9,.7)

    def test_generates_bounded_evidence_lineage(self):
        rows=EvidenceGuidedCompositionPlanner(budget=4).propose(
            self.gap(),["planner","retrieval","verifier"])
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r.required_evidence for r in rows))
        self.assertTrue(all("novel-domain" in r.validation_dimensions for r in rows))

    def test_digests_are_deterministic(self):
        p=EvidenceGuidedCompositionPlanner(budget=2)
        a=p.propose(self.gap(),["planner","retrieval"])
        b=p.propose(self.gap(),["planner","retrieval"])
        self.assertEqual([x.proposal_digest for x in a],[x.proposal_digest for x in b])

    def test_requires_capabilities(self):
        with self.assertRaises(ValueError):
            EvidenceGuidedCompositionPlanner().propose(self.gap(),[])

if __name__=="__main__":
    unittest.main()
