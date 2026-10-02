import unittest
from portable.failure_cluster_invention import FailureClusterCapabilityInventor

class FailureClusterTests(unittest.TestCase):
    def test_requires_repeated_cluster(self):
        c=FailureClusterCapabilityInventor(min_samples=3)
        self.assertEqual(c.propose([{"trigger":"timeout","components":["verify","parallel"]}]),())

    def test_proposes_bounded_candidate(self):
        c=FailureClusterCapabilityInventor(min_samples=2,max_candidates=2)
        rows=[{"trigger":"timeout","components":["verify","parallel"],"uncertainty":.8},
              {"trigger":"timeout","components":["verify","parallel"],"uncertainty":.6},
              {"trigger":"timeout","components":["verify","parallel"],"uncertainty":.7}]
        out=c.propose(rows)
        self.assertEqual(len(out),1)
        self.assertGreater(out[0].priority,0)

if __name__=="__main__": unittest.main()
