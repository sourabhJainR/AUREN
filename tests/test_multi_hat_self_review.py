import unittest
from portable.multi_hat_self_review import MultiHatSelfReview, ReviewFinding, ReviewHat


class MultiHatSelfReviewTests(unittest.TestCase):
    def test_all_hats_run_and_findings_remain_advisory(self):
        reviewer = MultiHatSelfReview()
        calls = []
        reviewers = {}
        for hat in reviewer.HATS:
            def make(h):
                def run(implementation, changed, evidence):
                    calls.append(h)
                    return (ReviewFinding(h, "warning", h.value, "finding", "address"),)
                return run
            reviewers[hat] = make(hat)
        report = reviewer.review(
            implementation="implemented feature",
            changed_files=("a.py",),
            evidence_ids=("e1",),
            reviewers=reviewers,
        )
        self.assertEqual(set(calls), set(reviewer.HATS))
        self.assertTrue(report.has_findings)
        self.assertEqual(report.developer_decision, "pending")
        self.assertEqual(report.decide("address").developer_decision, "address")

    def test_missing_hat_is_rejected(self):
        reviewer = MultiHatSelfReview()
        with self.assertRaises(ValueError):
            reviewer.review(
                implementation="x",
                changed_files=("a.py",),
                evidence_ids=("e1",),
                reviewers={},
            )


if __name__ == "__main__":
    unittest.main()
