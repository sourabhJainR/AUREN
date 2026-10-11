"""Default execution-controller promotion gate regression tests."""
import copy
import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController

REPOSITORY = "owner/project"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
APPROVER = "release-manager"
_PROMOTION_CONTEXT = {}
_PROMOTION_SIDE_EFFECTS = []


def trusted_promotion_context(request):
    # The provider deliberately receives only task identity, never task payload.
    return copy.deepcopy(_PROMOTION_CONTEXT)


def protected_promotion_handler(payload):
    _PROMOTION_SIDE_EFFECTS.append(payload.get("operation", "promote"))
    return {"success": True, "detail": "promotion handler ran", "evidence": ["promotion-action"]}


def valid_record(head=HEAD_SHA):
    return {
        "schema_version": "1.0",
        "task_id": "execution-controller-promotion-test",
        "repository": REPOSITORY,
        "base_commit": BASE_SHA,
        "head_commit": head,
        "risk_level": "high",
        "acceptance_criteria": ["Exact-head CI passed", "Canary evidence is canonical"],
        "selected_option": "Bounded reversible promotion",
        "selection_rationale": "Promote only with verified decision evidence",
        "alternatives": [{"option": "Immediate merge", "reason_rejected": "Bypasses trusted gate"}],
        "dependencies": [],
        "checks": [{
            "name": "required-ci",
            "command": "ci",
            "status": "passed",
            "commit_sha": head,
            "evidence_id": "run-1",
            "evaluator_type": "internal",
        }],
        "limitations": ["External generalization remains unproven"],
        "rollback_trigger": "Any required check or canary fails",
        "rollback_action": "Restore last known-good revision",
        "retry_history": [],
        "promotion": {
            "level": "verified_for_scope",
            "approved_by": APPROVER,
            "approval_evidence_id": "approval-1",
        },
    }


def valid_context():
    return {
        "decision_record": valid_record(),
        "expected_repository": REPOSITORY,
        "expected_head": HEAD_SHA,
        "authorized_approvers": [APPROVER],
        "known_evidence_ids": ["run-1", "approval-1"],
    }


class ExecutionControllerPromotionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _PROMOTION_SIDE_EFFECTS.clear()
        _PROMOTION_CONTEXT.clear()
        _PROMOTION_CONTEXT.update(valid_context())
        self.controller = ExecutionController(Path(self.temp.name))
        self.controller.register_handler("trusted-context", trusted_promotion_context)
        self.controller.register_handler(
            "promote-capability",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
        )

    def submit(self, payload=None):
        return self.controller.submit(
            "promote-capability", payload or {"operation": "merge"}
        )

    def test_valid_decision_record_allows_handler(self):
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "completed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, ["merge"])

    def test_missing_record_blocks_before_handler_side_effects(self):
        _PROMOTION_CONTEXT["decision_record"] = None
        task = self.submit({"operation": "merge", "promotion_gate": False})
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_stale_record_cannot_be_overridden_by_task_payload(self):
        _PROMOTION_CONTEXT["decision_record"] = valid_record(head="c" * 40)
        task = self.submit({
            "operation": "merge",
            "expected_head": HEAD_SHA,
            "promotion_gate": False,
            "decision_record": valid_record(),
        })
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_re_registration_without_policy_does_not_remove_gate(self):
        self.controller.register_handler("promote-capability", protected_promotion_handler)
        _PROMOTION_CONTEXT["decision_record"] = None
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_gate_policy_requires_registered_trusted_context_provider(self):
        with self.assertRaisesRegex(KeyError, "unknown durable handler"):
            self.controller.register_handler(
                "bad-promoter",
                protected_promotion_handler,
                promotion_gate_policy={"context_handler": "missing-provider"},
            )


if __name__ == "__main__":
    unittest.main()
