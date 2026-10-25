"""Compose AUREN action authorization with the optional OpenShell boundary.

This adapter prevents sandbox launch unless the host's governance policy allows the
exact action. It emits a privacy-conscious receipt linking the policy verdict to the
sandbox's policy/workspace digests and exit status. It does not itself prove that the
OpenShell gateway or kernel isolation is trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .action_governance import (
    ActionGovernanceGateway,
    ActionRequest,
    ApprovalReceipt,
    GovernedResult,
    PolicyVerdict,
)
from .execution_boundary import ExecutionAuthorityBinding, execute_bound_action
from .runtime_recovery import RuntimeExecutionGuard
from .openshell_sandbox import (
    OpenShellExecutionRequest,
    OpenShellExecutionResult,
    OpenShellSandboxBackend,
)


@dataclass(frozen=True)
class GovernedSandboxReceipt:
    """Receipt for the policy decision and bounded sandbox invocation."""

    action_executed: bool
    policy_verdict: PolicyVerdict
    sandbox_evidence: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return (
            self.action_executed
            and self.policy_verdict.allowed
            and self.sandbox_evidence is not None
            and self.sandbox_evidence.get("passed") is True
        )

    def as_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "auren.governed_sandbox_execution",
            "action_executed": self.action_executed,
            "passed": self.passed,
            "policy_decision": self.policy_verdict.decision,
            "policy_reason": self.policy_verdict.reason,
            "request_fingerprint": self.policy_verdict.request_fingerprint,
            "sandbox": dict(self.sandbox_evidence) if self.sandbox_evidence is not None else None,
        }


def execute_governed_sandbox(
    gateway: ActionGovernanceGateway,
    backend: OpenShellSandboxBackend,
    action: ActionRequest,
    sandbox_request: OpenShellExecutionRequest,
    *,
    approval: ApprovalReceipt | None = None,
    authority: ExecutionAuthorityBinding | None = None,
    repository_revision: str | None = None,
    repository_digest: str | None = None,
    budget_id: str | None = None,
    execution_guard: RuntimeExecutionGuard | None = None,
) -> GovernedSandboxReceipt:
    """Authorize before invoking OpenShell; never fall back to host execution.

    The caller remains responsible for ensuring that action.target and the
    curated workspace represent the same approved repository scope. The adapter
    deliberately does not infer that mapping from path names.
    """
    captured: list[OpenShellExecutionResult] = []
    handler = lambda: captured.append(backend.execute(sandbox_request)) or captured[-1].as_evidence()
    binding_args = (authority, repository_revision, repository_digest, budget_id, execution_guard)
    if authority is None:
        if any(value is not None for value in binding_args[1:]):
            raise ValueError("authority is required when execution-binding arguments are supplied")
        durable_verdict = gateway.durable_side_effect_preflight(action)
        binding_verdict = (
            None
            if durable_verdict is not None
            else gateway.unbound_external_effect_preflight(action)
        )
        blocked_verdict = durable_verdict or binding_verdict
        result = (
            GovernedResult(False, blocked_verdict)
            if blocked_verdict is not None
            else gateway.execute(action, handler, approval=approval)
        )
    else:
        if (
            not isinstance(repository_revision, str) or not repository_revision.strip()
            or not isinstance(repository_digest, str) or not repository_digest.strip()
            or not isinstance(budget_id, str) or not budget_id.strip()
            or not isinstance(execution_guard, RuntimeExecutionGuard)
        ):
            raise ValueError(
                "authority-bound sandbox execution requires repository_revision, "
                "repository_digest, budget_id, and the task-owned execution_guard"
            )
        result = execute_bound_action(
            gateway,
            authority,
            action,
            handler,
            repository_revision=repository_revision,
            repository_digest=repository_digest,
            budget_id=budget_id,
            execution_guard=execution_guard,
            approval=approval,
        )
    if not result.executed:
        return GovernedSandboxReceipt(False, result.verdict, None)
    if not captured:
        raise RuntimeError("governed sandbox invocation returned without a result")
    return GovernedSandboxReceipt(
        True,
        result.verdict,
        captured[0].as_evidence(),
    )


__all__ = ["GovernedSandboxReceipt", "execute_governed_sandbox"]
