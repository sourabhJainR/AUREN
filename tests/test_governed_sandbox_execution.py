import unittest
from types import SimpleNamespace

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.execution_boundary import ExecutionAuthorityBinding
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.governed_sandbox_execution import execute_governed_sandbox
from portable.openshell_sandbox import OpenShellExecutionRequest


class FakeBackend:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or SimpleNamespace(
            passed=True,
            as_evidence=lambda: {
                "backend": "nvidia-openshell",
                "passed": True,
                "policy_digest": "p" * 64,
                "workspace_digest": "w" * 64,
                "stdout_digest": "o" * 64,
                "stderr_digest": "e" * 64,
                "stdout_bytes": 12,
                "stderr_bytes": 0,
            },
        )
        self.error = error

    def execute(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


def make_action(**overrides):
    values = dict(
        run_id="run-1",
        action_id="sandbox-1",
        actor="auren-host",
        capability="repository.write",
        action="specialist.execute",
        target="src/service.py",
        risk="write",
    )
    values.update(overrides)
    return ActionRequest(**values)


def make_request():
    return OpenShellExecutionRequest(
        name_prefix="auren-test",
        workspace="/tmp/curated-workspace",
        policy_file="/tmp/openshell-policy.yaml",
        command=("python", "-m", "pytest"),
        timeout_seconds=10,
    )


class GovernedSandboxExecutionTests(unittest.TestCase):
    def make_gateway(self):
        return ActionGovernanceGateway(ActionPolicy(
            allowed_capabilities=frozenset({"repository.read", "repository.write"}),
            protected_targets=(".github/workflows", "main"),
        ))

    def test_denied_action_never_invokes_sandbox(self):
        gateway = self.make_gateway()
        backend = FakeBackend()
        receipt = execute_governed_sandbox(
            gateway, backend, make_action(target=".github/workflows/release.yml"), make_request()
        )
        self.assertFalse(receipt.action_executed)
        self.assertFalse(receipt.passed)
        self.assertEqual(receipt.policy_verdict.decision, "approval_required")
        self.assertEqual(backend.calls, [])
        self.assertIsNone(receipt.sandbox_evidence)
        self.assertTrue(gateway.verify_trace()["passed"])

    def test_allowed_action_links_governance_to_low_leakage_sandbox_evidence(self):
        gateway = self.make_gateway()
        backend = FakeBackend()
        receipt = execute_governed_sandbox(gateway, backend, make_action(), make_request())
        self.assertTrue(receipt.action_executed)
        self.assertTrue(receipt.passed)
        self.assertEqual(len(backend.calls), 1)
        evidence = receipt.as_evidence()
        self.assertEqual(evidence["kind"], "auren.governed_sandbox_execution")
        self.assertEqual(evidence["policy_decision"], "allow")
        self.assertEqual(evidence["sandbox"]["backend"], "nvidia-openshell")
        self.assertNotIn("'stdout':", repr(evidence))
        self.assertNotIn("'stderr':", repr(evidence))
        self.assertIn("stdout_digest", evidence["sandbox"])
        self.assertIn("stderr_digest", evidence["sandbox"])
        self.assertEqual([e["event"] for e in gateway.events], [
            "policy_verdict", "action_started", "action_completed"
        ])
        self.assertTrue(gateway.verify_trace()["passed"])

    def make_authority(self, **overrides):
        values = dict(
            execution_id="exec-1",
            run_id="run-1",
            task_id="task-1",
            repository_digest="repo-digest",
            repository_revision="abc123",
            principal="auren-host",
            allowed_capabilities=("repository.write",),
            allowed_actions=("specialist.execute",),
            allowed_targets=("src",),
            budget_id="budget-1",
            max_attempts=3,
        )
        values.update(overrides)
        return ExecutionAuthorityBinding(**values)

    def test_authority_bound_sandbox_rejects_mismatched_scope_before_backend(self):
        gateway = self.make_gateway()
        backend = FakeBackend()
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        receipt = execute_governed_sandbox(
            gateway, backend, make_action(task_id="task-1", target="secrets/credentials.json"), make_request(),
            authority=self.make_authority(),
            repository_revision="abc123",
            repository_digest="repo-digest",
            budget_id="budget-1",
            execution_guard=guard,
        )
        self.assertFalse(receipt.action_executed)
        self.assertEqual(receipt.policy_verdict.reason, "target_out_of_scope")
        self.assertEqual(backend.calls, [])
        self.assertEqual(guard.attempts_used, 0)

    def test_authority_bound_sandbox_charges_budget_before_gateway_execution(self):
        gateway = self.make_gateway()
        backend = FakeBackend()
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        receipt = execute_governed_sandbox(
            gateway, backend, make_action(task_id="task-1"), make_request(),
            authority=self.make_authority(),
            repository_revision="abc123",
            repository_digest="repo-digest",
            budget_id="budget-1",
            execution_guard=guard,
        )
        self.assertTrue(receipt.action_executed)
        self.assertTrue(receipt.passed)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(guard.attempts_used, 1)

    def test_authority_binding_arguments_cannot_be_supplied_partially(self):
        with self.assertRaisesRegex(ValueError, "authority is required"):
            execute_governed_sandbox(
                self.make_gateway(), FakeBackend(), make_action(), make_request(),
                repository_revision="abc123",
            )

    def test_nonzero_sandbox_result_is_not_reported_as_passed(self):
        gateway = self.make_gateway()
        failed = SimpleNamespace(
            passed=False,
            as_evidence=lambda: {
                "backend": "nvidia-openshell",
                "passed": False,
                "exit_code": 2,
                "policy_digest": "p" * 64,
                "workspace_digest": "w" * 64,
            },
        )
        receipt = execute_governed_sandbox(gateway, FakeBackend(result=failed), make_action(), make_request())
        self.assertTrue(receipt.action_executed)
        self.assertFalse(receipt.passed)
        self.assertFalse(receipt.as_evidence()["sandbox"]["passed"])

    def test_unavailable_sandbox_failure_propagates_and_is_traced(self):
        gateway = self.make_gateway()
        backend = FakeBackend(error=RuntimeError("OpenShell unavailable"))
        with self.assertRaisesRegex(RuntimeError, "OpenShell unavailable"):
            execute_governed_sandbox(gateway, backend, make_action(), make_request())
        self.assertEqual(gateway.events[-1]["event"], "action_failed")
        self.assertNotIn("OpenShell unavailable", repr(gateway.events))
        self.assertTrue(gateway.verify_trace()["passed"])


if __name__ == "__main__":
    unittest.main()
