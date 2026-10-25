"""Regression tests for P0 inventory, P2 authority binding, and P3 resume safety."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.side_effect_ledger import DurableSideEffectLedger
from portable.execution_boundary import (
    DIRECT_GATEWAY_CALLSITE_POLICY,
    ExecutionAuthorityBinding,
    ExecutionCheckpointConflict,
    ExecutionCheckpointStore,
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

    def test_legacy_action_fingerprint_is_unchanged_without_task_binding(self):
        request = ActionRequest(
            run_id="run-legacy", action_id="action-legacy", actor="agent",
            capability="source.read", action="read", target="src/module.py",
        )
        legacy_payload = asdict(request)
        legacy_payload.pop("task_id", None)
        expected = hashlib.sha256(
            json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.assertEqual(request.fingerprint(), expected)

    def test_p0_inventory_is_deterministic_and_discloses_assurance_gaps(self):
        first = execution_surface_audit()
        second = execution_surface_audit()
        self.assertEqual(first["digest"], second["digest"])
        self.assertGreaterEqual(len(first["surfaces"]), 5)
        self.assertIn("not_proven_by_repository_inventory", first["coverage"]["trusted_isolation"])
        self.assertTrue(any("callers" in gap for gap in first["gaps"]))
        action_surface = next(surface for surface in first["surfaces"] if surface["name"] == "action-authorization")
        self.assertIn("OpenShell", action_surface["existing_control"])
        self.assertIn("coding-agency", action_surface["existing_control"])
        self.assertIn("specialist-team", action_surface["existing_control"])
        self.assertIn("not yet mandatory", action_surface["remaining_gap"])

    def test_p0_every_direct_gateway_execute_callsite_is_explicitly_reviewed(self):
        root = Path(__file__).resolve().parents[1]
        portable_root = root / "portable"
        actual = set()

        class GatewayCallVisitor(ast.NodeVisitor):
            def __init__(self, relative_path):
                self.relative_path = relative_path
                self.scope = []

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "execute"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in {"gateway", "governance_gateway"}
                ):
                    actual.add((self.relative_path, ".".join(self.scope)))
                self.generic_visit(node)

        for source in sorted(portable_root.rglob("*.py")):
            relative_path = source.relative_to(root).as_posix()
            visitor = GatewayCallVisitor(relative_path)
            visitor.visit(ast.parse(source.read_text(encoding="utf-8"), filename=relative_path))

        expected = {(item["path"], item["scope"]) for item in DIRECT_GATEWAY_CALLSITE_POLICY}
        self.assertEqual(
            actual,
            expected,
            "New or moved direct gateway calls require explicit review and a bound-mode regression test.",
        )
        self.assertEqual(len(DIRECT_GATEWAY_CALLSITE_POLICY), len(expected))
        self.assertTrue(all(item["disposition"].strip() for item in DIRECT_GATEWAY_CALLSITE_POLICY))
        self.assertEqual(
            sum(item["disposition"] == "canonical-after-authority-preflight" for item in DIRECT_GATEWAY_CALLSITE_POLICY),
            1,
        )
        audit = execution_surface_audit()
        self.assertEqual(audit["direct_gateway_call_sites"], [dict(item) for item in DIRECT_GATEWAY_CALLSITE_POLICY])

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
            budget_id="budget-1", execution_guard=RuntimeExecutionGuard(max_steps=10, max_attempts=3),
        )
        self.assertFalse(denied.executed)
        self.assertEqual(denied.verdict.reason, "principal_mismatch")
        self.assertEqual(calls, [])

        accepted = execute_bound_action(
            gateway, binding, self.request(), lambda: calls.append("ran") or "ok",
            repository_revision="abc123", repository_digest="repo-digest",
            budget_id="budget-1", execution_guard=RuntimeExecutionGuard(max_steps=10, max_attempts=3),
        )
        self.assertTrue(accepted.executed)
        self.assertEqual(calls, ["ran"])

    def test_p2_rejects_absolute_paths_and_non_finite_expiry(self):
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            self.binding(allowed_targets=("/tmp",)).validate()
        with self.assertRaisesRegex(ValueError, "finite timestamp"):
            self.binding(expires_at=math.nan).validate()
        allowed, reason = self.binding().authorize(
            self.request(target="/workspace/src/module.py"),
            repository_revision="abc123", repository_digest="repo-digest",
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "invalid_target_path")

    def test_p2_binding_digest_changes_when_scope_changes(self):
        self.assertNotEqual(
            self.binding().digest,
            self.binding(allowed_actions=("read",)).digest,
        )

    def test_p3_checkpoint_restores_budget_only_for_same_authority_and_revision(self):
        binding = self.binding()
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3, deadline_seconds=30)
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

    def test_p3_capture_rejects_budget_larger_than_authority(self):
        with self.assertRaisesRegex(ValueError, "attempt budget exceeds execution authority"):
            GuardedExecutionCheckpoint.capture(
                self.binding(max_attempts=2),
                RuntimeExecutionGuard(max_steps=5, max_attempts=3),
                sequence=1,
            )

    def test_p3_restore_rejects_oversized_nested_budget_even_if_checkpoint_digest_is_valid(self):
        binding = self.binding(max_attempts=2)
        oversized_guard = RuntimeExecutionGuard(max_steps=5, max_attempts=3)
        checkpoint = GuardedExecutionCheckpoint(
            authority_digest=binding.digest,
            repository_revision=binding.repository_revision,
            sequence=1,
            guard_snapshot=oversized_guard.snapshot(),
        )
        with self.assertRaisesRegex(ValueError, "attempt budget exceeds execution authority"):
            checkpoint.restore(binding, expected_repository_revision="abc123")

    def test_p3_checkpoint_decoder_rejects_unknown_fields_and_invalid_sequence_floor(self):
        binding = self.binding()
        checkpoint = GuardedExecutionCheckpoint.capture(
            binding, RuntimeExecutionGuard(max_steps=5, max_attempts=2), sequence=1,
        )
        encoded = checkpoint.to_dict()
        encoded["untrusted_extra"] = "ignored-field-attack"
        with self.assertRaisesRegex(ValueError, "missing or unknown fields"):
            GuardedExecutionCheckpoint.from_dict(encoded)
        with self.assertRaisesRegex(ValueError, "minimum_sequence"):
            checkpoint.restore(binding, expected_repository_revision="abc123", minimum_sequence=-1)
        with self.assertRaisesRegex(ValueError, "minimum_sequence"):
            checkpoint.restore(binding, expected_repository_revision="abc123", minimum_sequence=True)

    def test_p3_durable_store_enforces_monotonic_chain_and_restores_budget(self):
        binding = self.binding()
        with tempfile.TemporaryDirectory() as tmp:
            store = ExecutionCheckpointStore(f"{tmp}/execution-checkpoints.sqlite3")
            guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3, deadline_seconds=30)
            self.assertTrue(guard.charge_attempt("budget-1:action-1"))
            first = GuardedExecutionCheckpoint.capture(binding, guard, sequence=1)
            first_digest = store.save(first, binding)
            self.assertEqual(first_digest, first.digest)

            with self.assertRaisesRegex(ExecutionCheckpointConflict, "increase monotonically"):
                store.save(first, binding)

            self.assertTrue(guard.charge_attempt("budget-1:action-2"))
            second = GuardedExecutionCheckpoint.capture(
                binding, guard, sequence=2, previous_checkpoint_digest=first.digest,
            )
            store.save(second, binding)
            loaded = store.latest(binding)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.digest, second.digest)
            restored = store.restore_latest(binding, expected_repository_revision="abc123")
            self.assertEqual(restored.attempts_used, 2)
            self.assertFalse(restored.charge_attempt("budget-1:action-1"))

            fork = GuardedExecutionCheckpoint.capture(
                binding, guard, sequence=3, previous_checkpoint_digest=first.digest,
            )
            with self.assertRaisesRegex(ExecutionCheckpointConflict, "predecessor digest mismatch"):
                store.save(fork, binding)

    def test_p3_concurrent_checkpoint_writers_cannot_overwrite_same_sequence(self):
        binding = self.binding()
        with tempfile.TemporaryDirectory() as tmp:
            store = ExecutionCheckpointStore(f"{tmp}/execution-checkpoints.sqlite3")
            initial_guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
            initial_guard.charge_attempt("initial")
            first = GuardedExecutionCheckpoint.capture(binding, initial_guard, sequence=1)
            store.save(first, binding)

            guard_a = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
            guard_a.charge_attempt("writer-a")
            guard_b = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
            guard_b.charge_attempt("writer-b")
            guard_b.charge_attempt("writer-b-second")
            candidate_a = GuardedExecutionCheckpoint.capture(
                binding, guard_a, sequence=2, previous_checkpoint_digest=first.digest,
            )
            candidate_b = GuardedExecutionCheckpoint.capture(
                binding, guard_b, sequence=2, previous_checkpoint_digest=first.digest,
            )
            barrier = threading.Barrier(2)

            def attempt_save(checkpoint):
                barrier.wait(timeout=5)
                try:
                    return ("saved", store.save(checkpoint, binding))
                except ExecutionCheckpointConflict as exc:
                    return ("rejected", str(exc))

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(attempt_save, (candidate_a, candidate_b)))

            self.assertEqual(sum(status == "saved" for status, _ in outcomes), 1)
            self.assertEqual(sum(status == "rejected" for status, _ in outcomes), 1)
            rejected = next(message for status, message in outcomes if status == "rejected")
            self.assertIn("sequence must increase monotonically", rejected)
            latest = store.latest(binding)
            self.assertIsNotNone(latest)
            self.assertEqual(latest.sequence, 2)
            self.assertIn(latest.digest, {candidate_a.digest, candidate_b.digest})

    def test_p3_durable_store_rejects_checkpoint_for_another_authority(self):
        binding = self.binding()
        other = self.binding(principal="agent:other")
        with tempfile.TemporaryDirectory() as tmp:
            store = ExecutionCheckpointStore(f"{tmp}/execution-checkpoints.sqlite3")
            checkpoint = GuardedExecutionCheckpoint.capture(
                binding, RuntimeExecutionGuard(max_steps=3, max_attempts=2), sequence=0,
            )
            with self.assertRaisesRegex(ExecutionCheckpointConflict, "authority binding mismatch"):
                store.save(checkpoint, other)

    def test_p3_restored_budget_still_stops_and_does_not_reset_charges(self):
        binding = self.binding()
        guard = RuntimeExecutionGuard(max_steps=2, max_attempts=1)
        guard.charge_attempt("only-attempt")
        checkpoint = GuardedExecutionCheckpoint.capture(binding, guard, sequence=1)
        restored = checkpoint.restore(binding, expected_repository_revision="abc123")
        with self.assertRaises(ExecutionBudgetExceeded):
            restored.charge_attempt("second-attempt")


    def test_p2_bound_external_effect_requires_durable_ledger_before_charging_budget(self):
        binding = self.binding(allowed_actions=("read", "edit", "test", "publish"))
        gateway = ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"source.write"}),
            allowed_actions=frozenset({"edit", "publish"}),
        ))
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        calls = []
        request = self.request(action="publish", risk="external_effect", idempotency_key="publish:task-1")
        result = execute_bound_action(
            gateway, binding, request, lambda: calls.append(True),
            repository_revision="abc123", repository_digest="repo-digest",
            budget_id="budget-1", execution_guard=guard,
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.verdict.reason, "durable_idempotency_ledger_required")
        self.assertEqual(guard.attempts_used, 0)
        self.assertEqual(calls, [])

    def test_p2_bound_external_effect_uses_durable_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            binding = self.binding(allowed_actions=("read", "edit", "test", "publish"))
            gateway = ActionGovernanceGateway(
                ActionPolicy(
                    allowed_capabilities=frozenset({"source.write"}),
                    allowed_actions=frozenset({"edit", "publish"}),
                    require_durable_ledger_for_external_effects=True,
                ),
                side_effect_ledger=DurableSideEffectLedger(Path(tmp) / "effects.sqlite3"),
            )
            request = self.request(action="publish", risk="external_effect", idempotency_key="publish:task-1")
            result = execute_bound_action(
                gateway, binding, request, lambda: {"provider_id": "op-1"},
                repository_revision="abc123", repository_digest="repo-digest",
                budget_id="budget-1", execution_guard=RuntimeExecutionGuard(max_steps=10, max_attempts=3),
            )
            self.assertTrue(result.executed)


class SubprocessBoundaryInventoryTests(unittest.TestCase):
    """Require every direct Popen function/callsite to have a reviewed disposition.

    This inventory is a tripwire, not proof that a listed adapter is safe or
    that non-Popen execution mechanisms are covered.
    """

    REVIEWED_POPEN_DISPOSITIONS = {
        ".ai-harness/provider.py::main": "interactive provider process; inspect argv, environment, deadline and output lifecycle",
        ".ai-harness/resilient_provider.py::main": "retrying provider process; inspect safe-provider handoff and shared retry budget",
        "portable/bounded_subprocess.py::run_bounded_command": "bounded primitive; inspect timeout, output caps, process-tree cleanup and requested resource limits",
        "portable/container_sandboxed_arena.py::_run_one_case": "container-backed Arena; verify runtime flags and fail closed on container setup errors",
        "portable/docker_external_evaluator_executor.py::__init__": "container runtime lifecycle; verify fixed argv, cleanup and container policy",
        "portable/docker_isolated_evaluator.py::launch": "Docker evaluator; verify image, mounts, capabilities, egress and cleanup policy",
        "portable/external_evaluator_gateway.py::evaluate": "host-process mode is test-only and is not a security sandbox",
        "portable/isolated_arena_runner.py::policy": "policy-command runner; the supplied command is not itself proof of OS isolation",
        "portable/local_offload.py::run": "local offload; inspect argv, environment, workspace, timeout and cancellation",
        "portable/subprocess_provider.py::__call__": "command provider; clean environment, no shell, bounded timeout and process-tree controls",
        "portable/vibe_coding_host.py::__call__": "host adapter; inspect argv, safe environment, workspace, cancellation and output bounds",
    }
    REVIEWED_POPEN_CALL_COUNTS = {key: 1 for key in REVIEWED_POPEN_DISPOSITIONS}

    def test_every_direct_popen_callsite_has_an_exact_reviewed_disposition(self):
        root = Path(__file__).resolve().parents[1]
        scan_roots = (root / "portable", root / ".ai-harness")
        actual = {}

        class Visitor(ast.NodeVisitor):
            def __init__(self, relative_path):
                self.relative_path = relative_path
                self.scope = []

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "Popen"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"
                ):
                    key = self.relative_path + "::" + "::".join(self.scope)
                    actual[key] = actual.get(key, 0) + 1
                self.generic_visit(node)

        for scan_root in scan_roots:
            if not scan_root.exists():
                continue
            for source in sorted(scan_root.rglob("*.py")):
                relative = source.relative_to(root).as_posix()
                Visitor(relative).visit(ast.parse(source.read_text(encoding="utf-8"), filename=relative))

        self.assertEqual(
            actual,
            self.REVIEWED_POPEN_CALL_COUNTS,
            "New, moved, or duplicated Popen callsites require an explicit review and "
            "denial-before-launch regression test; do not weaken this assertion to pass CI.",
        )

    def test_registry_records_non_sandbox_and_runtime_policy_limits(self):
        doc = (Path(__file__).resolve().parents[1] / "docs/GUARDED_EXECUTION_BOUNDARY_FOLLOWUP.md").read_text(encoding="utf-8")
        self.assertTrue(all(value.strip() for value in self.REVIEWED_POPEN_DISPOSITIONS.values()))
        self.assertIn("not a security sandbox", doc)
        self.assertIn("not proof that every callsite within an already-listed module is authorized", doc)


if __name__ == "__main__":
    unittest.main()
