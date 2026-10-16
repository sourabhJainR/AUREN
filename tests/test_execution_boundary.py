"""Regression tests for P0 inventory, P2 authority binding, and P3 resume safety."""
from __future__ import annotations

import unittest

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.execution_boundary import (
    ExecutionAuthorityBinding,
    GuardedExecutionCheckpoint,
    execution_surface_audit,
    execute_bound_action,
)
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard


class ExecutionBoundaryTests(unittest.TestCase):
    def binding(self, **overrides):
        values = dict(
            execution_id="exec-1",
            run_id="run-1",
            task_id="task-1",
            repository_digest="repo-digest",
            repository_revision="abc123",
            principal="agent:reviewed-worker",
            allowed_capabilities=("source.read", "source.write"),
            allowed_actions=("read", "edit", "test"),
            allowed_targets=("src", "tests"),
            budget_id="budget-1",
            max_attempts=3,
        )
        values.update(overrides)
        return ExecutionAuthorityBinding(**values)

    def request(self, **overrides):
        values = dict(
            run_id="run-1", task_id="task-1", action_id="action-1", actor="agent:reviewed-worker",
            capability="source.write", action="edit", target="src/module.py",
        )
        values.update(overrides)
        return ActionRequest(**values)

    def test_p0_inventory_is_deterministic_and_discloses_assurance_gaps(self):
        first = execution_surface_audit()
        second = execution_surface_audit()
        self.assertEqual(first["digest"], second["digest"])
        self.assertGreaterEqual(len(first["surfaces"]), 5)
        self.assertIn("not_proven_by_repository_inventory", first["coverage"]["trusted_isolation"])
        self.assertTrue(any("callers" in gap for gap in first["gaps"]))

    def test_p2_authority_binding_allows_only_matching_identity_scope_and_revision(self):
        binding = self.binding()
        allowed, reason = binding.authorize(self.request(), repository_revision="abc123", repository_digest="repo-digest")
        self.assertTrue(allowed, reason)

        cases = (
            (self.request(run_id="other-run"), "run_identity_mismatch"),
            (self.request(task_id="other-task"), "task_identity_mismatch"),
            (self.request(actor="agent:untrusted"), "principal_mismatch"),
            (self.request(capability="admin"), "capability_out_of_scope"),
            (self.request(action="release"), "action_out_of_scope"),
            (self.request(target="src-evil/secret.py"), "target_out_of_scope"),
        )
        for request, expected in cases:
            with self.subTest(expected=expected):
                allowed, reason = binding.authorize(request, repository_revision="abc123", repository_digest="repo-digest")
                self.assertFalse(allowed)
                self.assertEqual(reason, expected)

        allowed, reason = binding.authorize(self.request(), repository_revision="new-head", repository_digest="repo-digest")
        self.assertFalse(allowed)
        self.assertEqual(reason, "repository_revision_mismatch")
        allowed, reason = binding.authorize(self.request(), repository_revision="abc123", repository_digest="changed-digest")
        self.assertFalse(allowed)
        self.assertEqual(reason, "repository_digest_mismatch")

    def test_p2_budget_and_expiry_fail_closed(self):
        binding = self.binding(max_attempts=2, expires_at=50.0)
        allowed, reason = binding.authorize(self.request(), repository_revision="abc123", repository_digest="repo-digest", attempts_used=2, now=10.0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "attempt_budget_exhausted")
        allowed, reason = binding.authorize(self.request(), repository_revision="abc123", repository_digest="repo-digest", attempts_used=0, now=50.0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "execution_authority_expired")

    def test_p2_bound_execution_denies_before_handler_and_then_uses_gateway(self):
        binding = self.binding()
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"source.write"}),
            allowed_actions=frozenset({"edit"}),
        ))
        calls = []
        denied = execute_bound_action(
            gateway, binding, self.request(actor="agent:spoofed"),
            lambda: calls.append("ran"), repository_revision="abc123", repository_digest="repo-digest",
        )
        self.assertFalse(denied.executed)
        self.assertEqual(denied.verdict.reason, "principal_mismatch")
        self.assertEqual(calls, [])

        accepted = execute_bound_action(
            gateway, binding, self.request(), lambda: calls.append("ran") or "ok",
            repository_revision="abc123", repository_digest="repo-digest",
        )
        self.assertTrue(accepted.executed)
        self.assertEqual(calls, ["ran"])

    def test_p2_binding_digest_changes_when_scope_changes(self):
        self.assertNotEqual(
            self.binding().digest,
            self.binding(allowed_actions=("read",)).digest,
        )

    def test_p3_checkpoint_restores_budget_only_for_same_authority_and_revision(self):
        binding = self.binding()
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=4, deadline_seconds=30)
        self.assertTrue(guard.charge_attempt("task-1:step-1"))
        checkpoint = GuardedExecutionCheckpoint.capture(binding, guard, sequence=7)
        restored = checkpoint.restore(
            binding, expected_repository_revision="abc123", minimum_sequence=7,
        )
        self.assertEqual(restored.attempts_used, 1)
        self.assertFalse(restored.charge_attempt("task-1:step-1"))
        self.assertEqual(restored.attempts_used, 1)

        with self.assertRaisesRegex(ValueError, "authority binding mismatch"):
            checkpoint.restore(self.binding(principal="agent:other"), expected_repository_revision="abc123")
        with self.assertRaisesRegex(ValueError, "repository revision mismatch"):
            checkpoint.restore(binding, expected_repository_revision="def456")
        with self.assertRaisesRegex(ValueError, "replay or sequence rollback"):
            checkpoint.restore(binding, expected_repository_revision="abc123", minimum_sequence=8)

    def test_p3_checkpoint_serialization_detects_tampering(self):
        binding = self.binding()
        checkpoint = GuardedExecutionCheckpoint.capture(
            binding, RuntimeExecutionGuard(max_steps=5, max_attempts=2), sequence=1,
        )
        encoded = checkpoint.to_dict()
        restored = GuardedExecutionCheckpoint.from_dict(encoded)
        self.assertEqual(restored.digest, checkpoint.digest)
        encoded["sequence"] = 2
        with self.assertRaisesRegex(ValueError, "checkpoint digest mismatch"):
            GuardedExecutionCheckpoint.from_dict(encoded)

    def test_p3_restored_budget_still_stops_and_does_not_reset_charges(self):
        binding = self.binding()
        guard = RuntimeExecutionGuard(max_steps=2, max_attempts=1)
        guard.charge_attempt("only-attempt")
        checkpoint = GuardedExecutionCheckpoint.capture(binding, guard, sequence=1)
        restored = checkpoint.restore(binding, expected_repository_revision="abc123")
        with self.assertRaises(ExecutionBudgetExceeded):
            restored.charge_attempt("second-attempt")


if __name__ == "__main__":
    unittest.main()
