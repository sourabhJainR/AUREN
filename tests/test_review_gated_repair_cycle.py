import tempfile
import unittest
from pathlib import Path

from portable.multi_hat_self_review import ReviewFinding, ReviewHat
from portable.repository_engineering_cycle import PatchProposal
from portable.review_gated_repair_cycle import ReviewGatedRepairCycle
from portable.sandboxed_repository import CommandSpec


class ReviewGatedRepairCycleTests(unittest.TestCase):
    def test_successful_change_gets_full_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("print('old')\n")
            reviewers = {
                hat: (lambda h: lambda *_: (ReviewFinding(h, "info", "ok", "reviewed", "none"),))(hat)
                for hat in ReviewHat
            }
            result = ReviewGatedRepairCycle(str(root)).run(
                PatchProposal("change sample", {"sample.py": "print('new')\n"}),
                commands=(CommandSpec("run", ("python", "sample.py")),),
                reviewers=reviewers,
            )
            self.assertTrue(result.final.accepted)
            self.assertEqual(set(result.review.reviewed_hats), set(ReviewHat))


if __name__ == "__main__":
    unittest.main()
