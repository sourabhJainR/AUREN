import tempfile
import unittest
from pathlib import Path

from portable.learning_transfer import LearningTransfer
from portable.multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport
from portable.review_gated_repair_cycle import RepairCandidate
from portable.review_remediation import ReviewRemediationCycle
from portable.sandboxed_repository import CommandSpec
from portable.verified_repair_loop import RepairAttempt


def reviewers_without_original():
    return {
        hat: (lambda h: lambda *_: (ReviewFinding(h, "info", "post-check", "reviewed", "none"),))(hat)
        for hat in ReviewHat
    }


class FakeLearning:
    def __init__(self):
        self.calls = []

    def record_failure(self, **kwargs):
        self.calls.append(kwargs)


class ReviewRemediationTests(unittest.TestCase):
    def _report(self, finding):
        return SelfReviewReport(
            (finding,), tuple(ReviewHat), ("review-1",), "address"
        )

    def _candidate(self, finding, text="print('new')\n"):
        return RepairCandidate(
            RepairAttempt(1, finding.detail, 0.1, True, "verify-1"),
            __import__("portable").PatchProposal(
                "resolve: " + finding.title, {"sample.py": text}, model="test"
            ),
        )

    def test_stable_finding_ids_and_backlog(self):
        finding = ReviewFinding(
            ReviewHat.QUALITY, "high", "Quality gap", "missing regression", "add test", ("e1",)
        )
        self.assertEqual(finding.stable_id, ReviewFinding(
            ReviewHat.QUALITY, "high", "Quality gap", "missing regression", "add test", ("e1",)
        ).stable_id)
        with tempfile.TemporaryDirectory() as d:
            backlog = ReviewRemediationCycle(d).backlog(self._report(finding))
            self.assertEqual(backlog[0].finding_id, finding.stable_id)
            self.assertEqual(backlog[0].status, "pending")

    def test_stop_does_not_create_repair_work(self):
        finding = ReviewFinding(ReviewHat.SECURITY, "blocker", "Unsafe path", "bad path", "reject it")
        report = self._report(finding).decide("stop")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                ReviewRemediationCycle(d).run(
                    report, finding_ids=(finding.stable_id,),
                    commands=(CommandSpec("run", ("python", "sample.py")),),
                    reviewers=reviewers_without_original(),
                    initial=lambda f: self._candidate(f),
                )

    def test_selected_finding_is_resolved_only_after_verification_and_final_review(self):
        finding = ReviewFinding(
            ReviewHat.QUALITY, "high", "Quality gap", "missing regression", "add test", ("review-1",)
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("print('old')\n")
            result = ReviewRemediationCycle(str(root)).run(
                self._report(finding),
                finding_ids=(finding.stable_id,),
                commands=(CommandSpec("run", ("python", "sample.py")),),
                reviewers=reviewers_without_original(),
                initial=lambda f: self._candidate(f),
            )
            self.assertTrue(result.backlog[0].repair.final.accepted, result.backlog[0].repair.final.rejection_reason + " / " + result.backlog[0].repair.final.execution.failure)
            item = result.backlog[0]
            self.assertEqual(item.status, "resolved")
            self.assertEqual(item.changed_files, ("sample.py",))
            self.assertTrue(item.verification_evidence_ids)
            self.assertIsNotNone(item.final_review)

    def test_original_finding_reopens_and_failed_remediation_becomes_negative_learning(self):
        finding = ReviewFinding(
            ReviewHat.SECURITY, "high", "Unsafe path", "unsafe write", "reject unsafe path", ("review-2",)
        )
        def same_reviewers():
            return {
                hat: (lambda h: lambda *_: (
                    ReviewFinding(h, "high", "Unsafe path", "still unsafe", "reject unsafe path", ("verify-2",))
                    if h == ReviewHat.SECURITY else
                    ReviewFinding(h, "info", "post-check", "reviewed", "none")
                ,))(hat)
                for hat in ReviewHat
            }

        learning = FakeLearning()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("print('old')\n")
            result = ReviewRemediationCycle(str(root)).run(
                self._report(finding),
                finding_ids=(finding.stable_id,),
                commands=(CommandSpec("run", ("python", "sample.py")),),
                reviewers=same_reviewers(),
                initial=lambda f: self._candidate(f),
                learning_transfer=learning,
            )
            self.assertEqual(result.unresolved, (finding.stable_id,))
            self.assertEqual(result.backlog[0].status, "unresolved")
            self.assertEqual(len(learning.calls), 1)
            self.assertIn("Remediation did not resolve", learning.calls[0]["dont"])

    def test_only_selected_findings_are_actionable(self):
        first = ReviewFinding(ReviewHat.QUALITY, "medium", "First", "d1", "r1")
        second = ReviewFinding(ReviewHat.PERFORMANCE, "low", "Second", "d2", "r2")
        report = SelfReviewReport((first, second), tuple(ReviewHat), ("e",), "address")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("print('old')\n")
            result = ReviewRemediationCycle(str(root)).run(
                report,
                finding_ids=(first.stable_id,),
                commands=(CommandSpec("run", ("python", "sample.py")),),
                reviewers=reviewers_without_original(),
                initial=lambda f: self._candidate(f),
            )
            self.assertTrue(result.backlog[0].repair.final.accepted, result.backlog[0].repair.final.rejection_reason + " / " + result.backlog[0].repair.final.execution.failure)
            self.assertEqual(result.skipped, (second.stable_id,))


if __name__ == "__main__":
    unittest.main()
