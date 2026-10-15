"""Integration regressions for rollback readiness on the default promotion path."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController
from tests.test_execution_controller_promotion_gate import (
    _CI_CONTEXT,
    _PROMOTION_CONTEXT,
    _PROMOTION_SIDE_EFFECTS,
    protected_promotion_handler,
    trusted_ci_evidence,
    trusted_promotion_context,
    valid_context,
)
from tests.test_execution_controller_promotion_gate import HEAD_SHA, REPOSITORY


class PromotionRollbackFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _PROMOTION_SIDE_EFFECTS.clear()
        _PROMOTION_CONTEXT.clear()
        _PROMOTION_CONTEXT.update(valid_context())
        _CI_CONTEXT.clear()
        _CI_CONTEXT.update({
            "head_sha": HEAD_SHA,
            "status": "completed",
            "conclusion": "success",
            "passed_checks": ["required-ci"],
            "skipped_checks": [],
        })
        self.controller = ExecutionController(Path(self.temp.name))
        for evidence_id, evidence_type in (
            ("run-1", "ci-run"),
            ("approval-1", "approval"),
            ("rollback-test-1", "rollback-rehearsal"),
        ):
            self.controller.evidence_ledger.record(
                evidence_id=evidence_id,
                repository=REPOSITORY,
                head_sha=HEAD_SHA,
                evidence_type=evidence_type,
                status="passed",
                source_uri=f"urn:auren:test-evidence:{evidence_id}",
                payload_digest="a" * 64,
                signer_id="test-operator",
            )
        self.controller.register_handler(
            "trusted-context", trusted_promotion_context, execution_class="context_provider"
        )
        self.controller.register_handler(
            "trusted-ci", trusted_ci_evidence, execution_class="context_provider"
        )
        self.controller.register_handler(
            "promote-capability",
            protected_promotion_handler,
            promotion_gate_policy={"context_handler": "trusted-context"},
            ci_gate_policy={
                "expected_head_sha": HEAD_SHA,
                "required_checks": ["required-ci"],
                "evidence_handler": "trusted-ci",
            },
            execution_class="consequential",
        )

    def _attempt(self) -> None:
        task = self.controller.submit(
            "promote-capability",
            {"operation": "merge", "promotion_gate": False, "decision_record": {"risk_level": "low"}},
        )
        self.controller.run_once(task.id)
        self.assertEqual(self.controller.task(task.id).state, "failed")
        self.assertEqual(_PROMOTION_SIDE_EFFECTS, [])

    def test_missing_rollback_action_blocks_before_side_effects(self) -> None:
        _PROMOTION_CONTEXT["decision_record"]["rollback_action"] = ""
        self._attempt()

    def test_missing_last_known_good_commit_blocks_high_risk_promotion(self) -> None:
        _PROMOTION_CONTEXT["decision_record"].pop("last_known_good_commit", None)
        self._attempt()

    def test_task_payload_cannot_replace_trusted_rollback_record(self) -> None:
        _PROMOTION_CONTEXT["decision_record"]["rollback_test_evidence_id"] = "unregistered-rollback"
        self._attempt()


if __name__ == "__main__":
    unittest.main()
