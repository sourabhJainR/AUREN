import tempfile
import unittest
from pathlib import Path
from portable.autonomous_engineering_loop import AutonomousEngineeringLoop
from portable.engineering_evolution import EngineeringEvolutionControlPlane
from portable.persistent_memory import PersistentMemory
from portable.multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport

class ClosedLoopTests(unittest.TestCase):
    def test_noop_is_terminal_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            memory=PersistentMemory(Path(d)/"m.sqlite", require_approval=False)
            cp=EngineeringEvolutionControlPlane(memory,"p")
            loop=AutonomousEngineeringLoop(cp)
            receipt=loop.run(episode_id="e1",task_family="coding",capability="testing",
                             execute=lambda p: None,verify=lambda r:(True,("v1",)))
            self.assertTrue(receipt.accepted)
            self.assertEqual(receipt.terminal_action,"complete")
            self.assertEqual(receipt.iterations,1)

    def test_risky_history_gates_before_execution(self):
        with tempfile.TemporaryDirectory() as d:
            memory=PersistentMemory(Path(d)/"m.sqlite", require_approval=False)
            cp=EngineeringEvolutionControlPlane(memory,"p")
            cp.backlog.upsert(finding_id="f",task_family="coding",capability="testing",
                hat="quality",severity="critical",title="bad",detail="bad",
                recommendation="fix",attempts_increment=4)
            called=[]
            receipt=AutonomousEngineeringLoop(cp).run(
                episode_id="e2",task_family="coding",capability="testing",
                execute=lambda p: called.append(1),verify=lambda r:(True,("v",)))
            self.assertFalse(receipt.accepted)
            self.assertEqual(receipt.terminal_action,"escalate")
            self.assertFalse(called)

if __name__=="__main__": unittest.main()

    def test_self_review_address_stops_before_learning_or_promotion(self):
        with tempfile.TemporaryDirectory() as d:
            memory = PersistentMemory(Path(d) / "m.sqlite", require_approval=False)
            cp = EngineeringEvolutionControlPlane(memory, "p")
            cp.backlog.upsert(
                finding_id="f1", task_family="coding", capability="testing",
                hat="quality", severity="medium", title="work", detail="work",
                recommendation="verify",
            )
            promoted = []
            learned = []

            def review(result, evidence):
                finding = ReviewFinding(
                    ReviewHat.SECURITY, "high", "Security gap", "needs review",
                    "address before promotion", evidence,
                )
                return SelfReviewReport(
                    (finding,), tuple(ReviewHat), tuple(evidence), "address"
                )

            receipt = AutonomousEngineeringLoop(cp).run(
                episode_id="e3", task_family="coding", capability="testing",
                execute=lambda p: "changed",
                verify=lambda r: (True, ("verify-1",)),
                learn=lambda r, e: learned.append(1),
                promote=lambda r, e: promoted.append(1) or True,
                self_review=review,
            )
            self.assertFalse(receipt.accepted)
            self.assertEqual(receipt.terminal_action, "self_review_address")
            self.assertFalse(learned)
            self.assertFalse(promoted)
            self.assertTrue(receipt.remediation_ids)

    def test_self_review_accept_allows_promotion(self):
        with tempfile.TemporaryDirectory() as d:
            memory = PersistentMemory(Path(d) / "m.sqlite", require_approval=False)
            cp = EngineeringEvolutionControlPlane(memory, "p")
            cp.backlog.upsert(
                finding_id="f2", task_family="coding", capability="testing",
                hat="quality", severity="medium", title="work", detail="work",
                recommendation="verify",
            )
            promoted = []
            finding = ReviewFinding(
                ReviewHat.QUALITY, "low", "Minor", "minor",
                "accept or address later", ("verify-2",),
            )
            report = SelfReviewReport((finding,), tuple(ReviewHat), ("verify-2",), "accept")
            receipt = AutonomousEngineeringLoop(cp).run(
                episode_id="e4", task_family="coding", capability="testing",
                execute=lambda p: "changed",
                verify=lambda r: (True, ("verify-2",)),
                promote=lambda r, e: promoted.append(1) or True,
                self_review=lambda r, e: report,
            )
            self.assertTrue(receipt.accepted)
            self.assertEqual(receipt.terminal_action, "promoted")
            self.assertEqual(promoted, [1])
