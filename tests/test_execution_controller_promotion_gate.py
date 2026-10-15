"""Default execution-controller promotion gate regression tests."""
import copy
import tempfile
import sys
import sqlite3
from unittest.mock import patch
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


def merge_pull_request_handler(payload):
    _PROMOTION_SIDE_EFFECTS.append(payload.get("operation", "merge"))
    return {"success": True, "detail": "merge handler ran"}


def trusted_ci_evidence(request):
    return copy.deepcopy(_CI_CONTEXT)


def unclassified_worker(payload):
    return dict(payload)


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
        "rollback_procedure_ref": "runbook://release-rollback",
        "rollback_test_evidence_id": "rollback-test-1",
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
        "known_evidence_ids": ["run-1", "approval-1", "rollback-test-1"],
    }


class ExecutionControllerPromotionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _PROMOTION_SIDE_EFFECTS.clear()
        _CI_CONTEXT.clear()
        _PROMOTION_CONTEXT.clear()
        _PROMOTION_CONTEXT.update(valid_context())
        _CI_CONTEXT.update({
            "head_sha": HEAD_SHA, "status": "completed", "conclusion": "success",
            "passed_checks": ["required-ci"], "skipped_checks": [],
        })
        self.controller = ExecutionController(Path(self.temp.name))
        for evidence_id, evidence_type in (("run-1", "ci-run"), ("approval-1", "approval"), ("rollback-test-1", "rollback-rehearsal")):
            self.controller.evidence_ledger.record(
                evidence_id=evidence_id, repository=REPOSITORY, head_sha=HEAD_SHA,
                evidence_type=evidence_type, status="passed",
                source_uri=f"urn:auren:test-evidence:{evidence_id}",
                payload_digest="a" * 64, signer_id="test-operator",
            )
        self.controller.register_handler("trusted-context", trusted_promotion_context, execution_class="context_provider")
        self.controller.register_handler("trusted-ci", trusted_ci_evidence, execution_class="context_provider")
        self.controller.register_handler(
            "promote-capability",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
            ci_gate_policy={"expected_head_sha": HEAD_SHA, "required_checks": ["required-ci"], "evidence_handler": "trusted-ci"},
            execution_class="consequential",
        )

    def submit(self, payload=None):
        return self.controller.submit(
            "promote-capability", payload or {"operation": "merge"}
        )

    def test_context_provider_cannot_be_submitted_as_a_task(self):
        with self.assertRaisesRegex(ValueError, "internal-only"):
            self.controller.submit("trusted-context", {"task_id": "attacker-controlled"})

    def test_unclassified_legacy_registration_fails_closed(self):
        import sqlite3
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute("UPDATE handlers SET execution_class='unclassified' WHERE name='promote-capability'")
        with self.assertRaisesRegex(ValueError, "unclassified legacy handler"):
            self.controller.submit("promote-capability", {"operation": "merge"})

    def test_handler_class_cannot_be_downgraded_on_reregistration(self):
        with self.assertRaisesRegex(ValueError, "execution_class is immutable"):
            self.controller.register_handler(
                "promote-capability", protected_promotion_handler, execution_class="read_only"
            )

    def test_classified_legacy_handler_without_digest_fails_closed(self):
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE handlers SET implementation_digest='' WHERE name=?",
                ("promote-capability",),
            )
        with self.assertRaisesRegex(ValueError, "has no implementation digest"):
            self.controller.register_handler(
                "promote-capability",
                protected_promotion_handler,
                execution_class="consequential",
                ci_gate_policy={
                    "expected_head_sha": HEAD_SHA,
                    "required_checks": ["required-ci"],
                    "evidence_handler": "trusted-ci",
                },
                promotion_gate_policy={"context_handler": "trusted-context"},
            )

    def test_loaded_handler_source_must_match_registered_implementation_digest(self):
        self.controller._load_handler("promote-capability")
        module = sys.modules[protected_promotion_handler.__module__]
        with patch.object(module, "protected_promotion_handler", unclassified_worker):
            with self.assertRaisesRegex(ValueError, "implementation digest mismatch"):
                self.controller._load_handler("promote-capability")

    def test_trusted_provider_implementation_cannot_be_swapped_under_same_identity(self):
        with self.assertRaisesRegex(ValueError, "handler reference is immutable"):
            self.controller.register_handler(
                "trusted-context", unclassified_worker, execution_class="context_provider"
            )

    def test_action_handler_cannot_be_mislabeled_read_only(self):
        with self.assertRaisesRegex(ValueError, "must be classified consequential"):
            self.controller.register_handler(
                "safe-helper", protected_promotion_handler, execution_class="read_only"
            )

    def test_arbitrary_handler_cannot_be_registered_as_internal_context_provider(self):
        with self.assertRaisesRegex(ValueError, "must have a context/evidence/provider"):
            self.controller.register_handler(
                "innocent-helper", unclassified_worker, execution_class="context_provider"
            )

    def test_new_handler_requires_explicit_execution_class(self):
        with self.assertRaisesRegex(ValueError, "requires an explicit execution_class"):
            self.controller.register_handler("unclassified-worker", unclassified_worker)

    def test_consequential_handler_rejects_unverified_promotion_levels(self):
        for level in ("none", "provisional", "invalid"):
            with self.subTest(level=level):
                _PROMOTION_CONTEXT.clear()
                _PROMOTION_CONTEXT.update(valid_context())
                _PROMOTION_CONTEXT["decision_record"]["promotion"]["level"] = level
                task = self.submit()
                self.controller.run_once(task.id)
                self.assertEqual(self.controller.task(task.id).state, "failed")
                self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

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
        self.controller.register_handler("promote-capability", protected_promotion_handler, execution_class="consequential")
        _PROMOTION_CONTEXT["decision_record"] = None
        task = self.submit()
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_consequential_handler_cannot_be_registered_without_promotion_gate(self):
        with self.assertRaisesRegex(ValueError, "requires an operator-owned promotion_gate_policy"):
            self.controller.register_handler("promote-without-policy", protected_promotion_handler, execution_class="consequential")

    def test_consequential_callable_cannot_bypass_gates_with_an_innocuous_alias(self):
        with self.assertRaisesRegex(ValueError, "requires an operator-owned promotion_gate_policy"):
            self.controller.register_handler("helper", protected_promotion_handler, execution_class="consequential")
        with self.assertRaisesRegex(ValueError, "requires an exact-head ci_gate_policy"):
            self.controller.register_handler(
                "helper", merge_pull_request_handler,
                promotion_gate_policy={"context_handler": "trusted-context"},
                execution_class="consequential",
            )

    def test_merge_handler_requires_exact_head_ci_and_promotion_gates(self):
        with self.assertRaisesRegex(ValueError, "requires an operator-owned promotion_gate_policy"):
            self.controller.register_handler("merge-pull-request", protected_promotion_handler, execution_class="consequential")
        with self.assertRaisesRegex(ValueError, "requires an exact-head ci_gate_policy"):
            self.controller.register_handler(
                "merge-pull-request", protected_promotion_handler,
                promotion_gate_policy={"context_handler": "trusted-context"},
                execution_class="consequential",
            )

    def test_gate_policy_requires_registered_trusted_context_provider(self):
        with self.assertRaisesRegex(KeyError, "unknown durable handler"):
            self.controller.register_handler(
                "bad-promoter",
                protected_promotion_handler,
                promotion_gate_policy={"context_handler": "missing-provider"},
                ci_gate_policy={"expected_head_sha": HEAD_SHA, "required_checks": ["required-ci"], "evidence_handler": "trusted-ci"},
                execution_class="consequential",
            )


    def test_provider_cannot_self_authorize_evidence_ids_and_unauthorized_approver_blocks(self):
        _PROMOTION_CONTEXT["known_evidence_ids"] = ["run-1", "approval-1", "rollback-test-1", "forged-id"]
        _PROMOTION_CONTEXT["decision_record"]["checks"][0]["evidence_id"] = "forged-id"
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
        _PROMOTION_CONTEXT["decision_record"]["promotion"]["approval_evidence_id"] = "unknown-approval"
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
        self.controller.register_handler("trusted-ci", trusted_ci_evidence, execution_class="context_provider")
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
            execution_class="consequential",
        )
        task = self.controller.submit("merge-pull-request", {"operation": "merge"})
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "completed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, ["merge"])

    def test_merge_rejects_in_progress_ci_even_if_payload_claims_success(self):
        self.controller.register_handler("trusted-ci", trusted_ci_evidence, execution_class="context_provider")
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
            execution_class="consequential",
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
        self.controller.register_handler("trusted-ci", trusted_ci_evidence, execution_class="context_provider")
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
            execution_class="consequential",
        )
        task = self.controller.submit("merge-pull-request", {"operation": "merge"})
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])



if __name__ == "__main__":
    unittest.main()
