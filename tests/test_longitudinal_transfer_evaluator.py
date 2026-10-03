import tempfile
import unittest
from pathlib import Path

from portable.longitudinal_transfer_evaluator import (
    LongitudinalTransferEvaluator, TransferObservation,
)


class LongitudinalTransferTests(unittest.TestCase):
    def test_requires_independent_novel_domains_and_holdout(self):
        observations = [
            TransferObservation("c1", "a", "domain-a", "novel", .9, .9),
            TransferObservation("c2", "b", "domain-b", "novel", .8, .9),
            TransferObservation("c3", "a", "domain-a", "holdout", .85, .9),
        ]
        profile = LongitudinalTransferEvaluator().evaluate("planner", observations)
        self.assertTrue(profile.trustworthy)
        self.assertEqual(profile.independent_domains, 2)
        self.assertAlmostEqual(profile.novel_domain_pass_rate, .85)
        self.assertAlmostEqual(profile.holdout_pass_rate, .85)

    def test_contaminated_and_nonindependent_results_are_excluded(self):
        observations = [
            TransferObservation("c1", "a", "domain-a", "novel", 1, 1, contaminated=True),
            TransferObservation("c2", "a", "domain-a", "novel", .9, .9, oracle_independent=False),
        ]
        with self.assertRaises(ValueError):
            LongitudinalTransferEvaluator().evaluate("planner", observations)

    def test_digest_is_deterministic(self):
        o = TransferObservation("c1", "a", "domain-a", "novel", .9, .9)
        p1 = LongitudinalTransferEvaluator().evaluate("planner", [o])
        p2 = LongitudinalTransferEvaluator().evaluate("planner", [o])
        self.assertEqual(p1.profile_digest, p2.profile_digest)


if __name__ == "__main__":
    unittest.main()
