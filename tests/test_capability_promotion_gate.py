"""Executable regression coverage for the decision gate at capability promotion."""
import tempfile
import unittest
from pathlib import Path

from portable.capability_lifecycle import CapabilityLifecycle
from portable.persistent_memory import PersistentMemory

REPO, BASE, HEAD, APPROVER = "owner/project", "a" * 40, "b" * 40, "release-manager"


def record():
    return {
        "schema_version": "1.0", "task_id": "gate-test", "repository": REPO,
        "base_commit": BASE, "head_commit": HEAD, "risk_level": "high",
        "acceptance_criteria": ["Canary suite passes"],
        "selected_option": "Bounded reversible promotion",
        "selection_rationale": "Smallest reversible change",
        "alternatives": [{"option": "Immediate rollout", "reason_rejected": "Not reversible"}],
        "dependencies": [],
        "checks": [{"name": "regression", "command": "unittest", "status": "passed",
                    "commit_sha": HEAD, "evidence_id": "run-1", "evaluator_type": "internal"}],
        "limitations": ["No independent campaign claimed"],
        "rollback_trigger": "Any required check fails",
        "rollback_action": "Restore last known-good capability and rerun holdout",
        "retry_history": [],
        "promotion": {"level": "verified_for_scope", "approved_by": APPROVER,
                      "approval_evidence_id": "approval-1"},
    }


class PromotionGateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def gate(self, name, decision=None, **changes):
        config = dict(
            min_canaries=1, promotion_record=decision, expected_repository=REPO,
            expected_head=HEAD, authorized_approvers={APPROVER},
            known_evidence_ids={"run-1", "approval-1"},
        )
        config.update(changes)
        return CapabilityLifecycle(
            PersistentMemory(self.root / f"{name}.db", require_approval=False), "p", **config
        )

    def promote(self, gate, name):
        gate.begin(name, baseline_score=0.7)
        return gate.record_canary(name, score=0.75, evidence_id=f"{name}-run")

    def test_valid_record_allows_promotion(self):
        result = self.promote(self.gate("valid", record()), "candidate")
        self.assertEqual(result.state, "promoted")

    def test_missing_record_stale_head_and_unknown_evidence_fail_closed(self):
        self.assertEqual(self.promote(self.gate("missing"), "missing").state, "promotion_blocked")
        stale = record()
        stale["head_commit"] = "c" * 40
        result = self.promote(self.gate("stale", stale), "stale")
        self.assertEqual(result.state, "promotion_blocked")
        self.assertTrue(any("stale evidence" in reason for reason in result.reasons))
        unknown = record()
        unknown["checks"][0]["evidence_id"] = "forged"
        result = self.promote(self.gate("unknown", unknown), "unknown")
        self.assertEqual(result.state, "promotion_blocked")
        self.assertTrue(any("unknown canonical evidence_id" in reason for reason in result.reasons))

    def test_unauthorized_approver_and_missing_rollback_block(self):
        unauthorized = record()
        unauthorized["promotion"]["approved_by"] = "untrusted"
        result = self.promote(self.gate("unauthorized", unauthorized), "unauthorized")
        self.assertEqual(result.state, "promotion_blocked")
        self.assertTrue(any("authorized approver" in reason for reason in result.reasons))
        no_rollback = record()
        no_rollback["rollback_action"] = ""
        result = self.promote(self.gate("rollback", no_rollback), "rollback")
        self.assertEqual(result.state, "promotion_blocked")
        self.assertTrue(any("rollback_action is required" in reason for reason in result.reasons))

    def test_failed_recovery_rolls_back_despite_valid_record(self):
        gate = self.gate("recovery", record())
        gate.begin("recovery", baseline_score=0.8)
        result = gate.record_canary("recovery", score=0.9, evidence_id="failed-recovery", safe=False)
        self.assertEqual(result.state, "rolled_back")


if __name__ == "__main__":
    unittest.main()
