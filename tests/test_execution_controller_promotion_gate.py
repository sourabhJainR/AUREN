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
_CI_CONTEXT = {}


def trusted_promotion_context(request):
    # The provider deliberately receives only task identity, never task payload.
    return copy.deepcopy(_PROMOTION_CONTEXT)


def protected_promotion_handler(payload):
    _PROMOTION_SIDE_EFFECTS.append(payload.get("operation", "promote"))
    return {"success": True, "detail": "promotion handler ran", "evidence": ["promotion-action"]}


def trusted_ci_evidence(request):
    return copy.deepcopy(_CI_CONTEXT)


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
        "last_known_good_commit": BASE_SHA,
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
        _CI_CONTEXT.clear()
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

    def test_consequential_handler_cannot_be_registered_without_promotion_gate(self):
        with self.assertRaisesRegex(ValueError, "requires an operator-owned promotion_gate_policy"):
            self.controller.register_handler("promote-without-policy", protected_promotion_handler)

    def test_merge_handler_requires_exact_head_ci_and_promotion_gates(self):
        with self.assertRaisesRegex(ValueError, "requires an operator-owned promotion_gate_policy"):
            self.controller.register_handler("merge-pull-request", protected_promotion_handler)
        with self.assertRaisesRegex(ValueError, "requires an exact-head ci_gate_policy"):
            self.controller.register_handler(
                "merge-pull-request", protected_promotion_handler,
                promotion_gate_policy={"context_handler": "trusted-context"},
            )

    def test_gate_policy_requires_registered_trusted_context_provider(self):
        with self.assertRaisesRegex(KeyError, "unknown durable handler"):
            self.controller.register_handler(
                "bad-promoter",
                protected_promotion_handler,
                promotion_gate_policy={"context_handler": "missing-provider"},
            )


    def test_unknown_evidence_and_unauthorized_approver_block_side_effects(self):
        _PROMOTION_CONTEXT["known_evidence_ids"] = ["approval-1"]
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

        _PROMOTION_CONTEXT.update(valid_context())
        _PROMOTION_CONTEXT["decision_record"]["promotion"]["approved_by"] = "untrusted-user"
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_missing_rollback_action_blocks_high_risk_promotion(self):
        _PROMOTION_CONTEXT["decision_record"].pop("rollback_action")
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_skipped_required_check_blocks_promotion(self):
        _PROMOTION_CONTEXT["decision_record"]["checks"][0]["status"] = "skipped"
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_unknown_approval_evidence_blocks_promotion(self):
        _PROMOTION_CONTEXT["known_evidence_ids"] = ["run-1"]
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_missing_rollback_anchor_blocks_high_risk_promotion(self):
        _PROMOTION_CONTEXT["decision_record"].pop("last_known_good_commit")
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_merge_allows_side_effect_only_after_matching_exact_head_ci(self):
        self.controller.register_handler("trusted-ci", trusted_ci_evidence)
        _CI_CONTEXT.update({
            "head_sha": HEAD_SHA,
            "status": "completed",
            "conclusion": "success",
            "passed_checks": ["required-ci"],
            "skipped_checks": [],
        })
        self.controller.register_handler(
            "merge-pull-request",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
            ci_gate_policy={
                "expected_head_sha": HEAD_SHA,
                "required_checks": ["required-ci"],
                "evidence_handler": "trusted-ci",
            },
        )
        task = self.controller.submit("merge-pull-request", {"operation": "merge"})
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "completed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, ["merge"])

    def test_merge_rejects_in_progress_ci_even_if_payload_claims_success(self):
        self.controller.register_handler("trusted-ci", trusted_ci_evidence)
        _CI_CONTEXT.update({
            "head_sha": HEAD_SHA,
            "status": "in_progress",
            "conclusion": None,
            "passed_checks": ["required-ci"],
            "skipped_checks": [],
        })
        self.controller.register_handler(
            "merge-pull-request",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
            ci_gate_policy={
                "expected_head_sha": HEAD_SHA,
                "required_checks": ["required-ci"],
                "evidence_handler": "trusted-ci",
            },
        )
        task = self.controller.submit(
            "merge-pull-request",
            {
                "operation": "merge",
                "ci_status": "completed",
                "ci_conclusion": "success",
                "passed_checks": ["required-ci"],
            },
        )
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_merge_blocks_when_ci_sha_differs_from_live_promotion_head(self):
        self.controller.register_handler("trusted-ci", trusted_ci_evidence)
        _CI_CONTEXT.update({
            "head_sha": BASE_SHA,
            "status": "completed",
            "conclusion": "success",
            "passed_checks": ["required-ci"],
            "skipped_checks": [],
        })
        self.controller.register_handler(
            "merge-pull-request",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
            ci_gate_policy={
                "expected_head_sha": BASE_SHA,
                "required_checks": ["required-ci"],
                "evidence_handler": "trusted-ci",
            },
        )
        task = self.controller.submit("merge-pull-request", {"operation": "merge"})
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])



if __name__ == "__main__":
    unittest.main()
