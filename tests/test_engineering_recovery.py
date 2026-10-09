from pathlib import Path
import tempfile
import unittest

from portable.engineering_recovery import (
    EngineeringRecoveryLedger,
    FailureClass,
    RecoveryAction,
    RecoveryLimitReached,
    verify_ci_gate,
)


class EngineeringRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_recovery_budget_forces_alternate_after_two_same_hypothesis_attempts(self):
        ledger = EngineeringRecoveryLedger(self.root / "recovery.sqlite", max_total_attempts=5)
        ledger.start_run("task-1", "implement a difficult feature")
        for index in range(2):
            attempt_id = ledger.begin_attempt(
                "task-1", "implementation", "test-fails:assert-7", "parser lacks precedence",
                action="narrow_fix", failure_class=FailureClass.CODE_DEFECT, head_sha=f"sha-{index}",
            )
            ledger.finish_attempt(attempt_id, outcome="failed", evidence=[f"test-run-{index}"])
        decision = ledger.decision("task-1", "implementation", "test-fails:assert-7", "parser lacks precedence")
        self.assertIs(decision.action, RecoveryAction.ALTERNATE_PLAN)
        with self.assertRaises(RecoveryLimitReached) as error:
            ledger.begin_attempt("task-1", "implementation", "test-fails:assert-7",
                                 "parser lacks precedence", action="narrow_fix")
        self.assertIs(error.exception.action, RecoveryAction.ALTERNATE_PLAN)
        next_attempt = ledger.begin_attempt(
            "task-1", "implementation", "test-fails:assert-7",
            "grammar ambiguity; split parser rule", action="alternate_implementation",
        )
        ledger.finish_attempt(next_attempt, outcome="passed", evidence=["focused regression passed"])
        self.assertEqual(len(ledger.attempts("task-1")), 3)

    def test_checkpoint_survives_new_ledger_instance(self):
        path = self.root / "recovery.sqlite"
        first = EngineeringRecoveryLedger(path)
        first.start_run("task-2", "restore after restart")
        first.checkpoint("task-2", "verify", status="in_progress", head_sha="abc123",
                         payload={"next_action": "rerun exact-head CI", "attempt": 2})
        checkpoint = EngineeringRecoveryLedger(path).get_checkpoint("task-2", "verify")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.head_sha, "abc123")
        self.assertEqual(checkpoint.payload["next_action"], "rerun exact-head CI")

    def test_intent_identity_cannot_be_reused_for_different_work(self):
        ledger = EngineeringRecoveryLedger(self.root / "recovery.sqlite")
        ledger.start_run("same-id", "original goal")
        with self.assertRaisesRegex(ValueError, "immutable intent"):
            ledger.start_run("same-id", "different goal")

    def test_ci_gate_rejects_stale_or_incomplete_ci(self):
        stale = verify_ci_gate(expected_head_sha="new-sha", ci_head_sha="old-sha", status="completed",
                               conclusion="success", required_checks=["tests"], passed_checks=["tests"])
        self.assertFalse(stale.accepted)
        pending = verify_ci_gate(expected_head_sha="same-sha", ci_head_sha="same-sha", status="in_progress",
                                 conclusion=None, required_checks=["tests"], passed_checks=[])
        self.assertFalse(pending.accepted)
        skipped = verify_ci_gate(expected_head_sha="same-sha", ci_head_sha="same-sha", status="completed",
                                 conclusion="success", required_checks=["tests"], passed_checks=["tests"],
                                 skipped_checks=["tests"])
        self.assertFalse(skipped.accepted)
        good = verify_ci_gate(expected_head_sha="same-sha", ci_head_sha="same-sha", status="completed",
                              conclusion="success", required_checks=["tests", "lint"],
                              passed_checks=["tests", "lint"])
        self.assertTrue(good.accepted)

    def test_total_budget_blocks_even_when_hypothesis_changes(self):
        ledger = EngineeringRecoveryLedger(self.root / "recovery.sqlite", max_total_attempts=1)
        ledger.start_run("task-3", "bounded")
        attempt_id = ledger.begin_attempt("task-3", "build", "failure-a", "hypothesis-a", action="reproduce")
        ledger.finish_attempt(attempt_id, outcome="failed")
        decision = ledger.decision("task-3", "build", "failure-b", "hypothesis-b")
        self.assertIs(decision.action, RecoveryAction.BLOCKED)

    def test_verified_checkpoint_requires_evidence_bound_to_exact_head(self):
        ledger = EngineeringRecoveryLedger(self.root / "recovery.sqlite")
        ledger.start_run("task-4", "verified evidence is required")
        with self.assertRaisesRegex(ValueError, "exact head SHA"):
            ledger.checkpoint("task-4", "verify", status="verified", head_sha="new-sha",
                              payload={"verified_head_sha": "old-sha", "verification_evidence": ["tests passed"]})
        with self.assertRaisesRegex(ValueError, "non-empty verification_evidence"):
            ledger.checkpoint("task-4", "verify", status="verified", head_sha="new-sha",
                              payload={"verified_head_sha": "new-sha"})
        checkpoint = ledger.checkpoint(
            "task-4", "verify", status="verified", head_sha="new-sha",
            payload={"verified_head_sha": "new-sha", "verification_evidence": ["focused tests passed"]},
        )
        self.assertEqual(checkpoint.status, "verified")


if __name__ == "__main__":
    unittest.main()
