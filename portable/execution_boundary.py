"""Execution-boundary inventory, authority binding, and safe resume contracts.

These contracts complement existing orchestration, ActionGovernanceGateway,
ExecutionEnvelope, and RuntimeExecutionGuard primitives. They do not replace
host authorization, trusted persistence, OS isolation, or downstream idempotency.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import posixpath
import time
from typing import Any, Mapping, Sequence

from .runtime_recovery import RuntimeExecutionGuard


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _normal_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target path is required")
    normalized = posixpath.normpath(value.replace("\\", "/").strip())
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise ValueError("target path must not escape its declared root")
    return normalized.rstrip("/") or "."


@dataclass(frozen=True)
class ExecutionSurface:
    """One existing execution surface and the evidence needed to trust it."""

    name: str
    responsibility: str
    implementation: str
    existing_control: str
    remaining_gap: str


# P0 inventory: a code-owned map of existing mechanisms, not an assertion that
# each mechanism is wired into every runtime path.
EXECUTION_SURFACE_INVENTORY: tuple[ExecutionSurface, ...] = (
    ExecutionSurface(
        "task-and-repository-identity", "Cross-phase task, plan, repository and evidence references",
        "portable/execution_contract.py:ExecutionEnvelope",
        "Schema validation, repository snapshot binding, deterministic envelope digest",
        "Envelope identity is not automatically required by every action gateway caller",
    ),
    ExecutionSurface(
        "action-authorization", "Capability/action allowlists and approval checks",
        "portable/action_governance.py:ActionGovernanceGateway",
        "Policy verdicts, approval binding, external-effect idempotency and tamper-evident event chain",
        "Callers must route consequential actions through the gateway; trace is not trusted storage",
    ),
    ExecutionSurface(
        "runtime-budget-and-cancellation", "Bounded steps, attempts, deadlines and repeated-failure stop",
        "portable/runtime_recovery.py:RuntimeExecutionGuard",
        "Thread-safe budget, cooperative cancellation, digest-checked snapshot/resume",
        "Durability depends on callers persisting checkpoints and workers honoring cancellation",
    ),
    ExecutionSurface(
        "engineering-recovery", "Cross-attempt recovery decisions and phase checkpoints",
        "portable/engineering_recovery.py:EngineeringRecoveryLedger",
        "SQLite-backed bounded attempts, time budget and exact-head CI gate helper",
        "Ledger does not itself sandbox a worker or guarantee downstream side-effect idempotency",
    ),
    ExecutionSurface(
        "provider-resume", "Provider call failover and checkpointed deferral",
        "portable/resumable_provider.py:ResumableProviderExecutor",
        "Pinned provider versions, task/stage identity, retry budget and persistence callbacks",
        "Irreversible effects require an external durable outbox/inbox and downstream idempotency",
    ),
)


def execution_surface_audit() -> dict[str, Any]:
    """Return a deterministic inventory report with explicit assurance boundaries."""
    surfaces = [
        {
            "name": item.name,
            "responsibility": item.responsibility,
            "implementation": item.implementation,
            "existing_control": item.existing_control,
            "remaining_gap": item.remaining_gap,
        }
        for item in EXECUTION_SURFACE_INVENTORY
    ]
    gaps = [item["remaining_gap"] for item in surfaces]
    report = {
        "schema_version": "auren-execution-surface-audit-v1",
        "surfaces": surfaces,
        "coverage": {
            "task_identity": "partial",
            "action_authority": "present_with_caller_enforcement",
            "budget_and_cancellation": "present_with_cooperative_cancellation",
            "durable_recovery": "present_with_persistence_dependency",
            "trusted_isolation": "not_proven_by_repository_inventory",
        },
        "gaps": gaps,
    }
    report["digest"] = _digest(report)
    return report


@dataclass(frozen=True)
class ExecutionAuthorityBinding:
    """Immutable execution identity and bounded authority for one run.

    Bind to a concrete repository revision and principal. This is an application
    guard: callers must still use ActionGovernanceGateway and enforce isolation
    outside this process.
    """

    execution_id: str
    run_id: str
    task_id: str
    repository_digest: str
    repository_revision: str
    principal: str
    allowed_capabilities: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    allowed_targets: tuple[str, ...]
    budget_id: str
    max_attempts: int
    expires_at: float | None = None
    schema_version: str = "auren-execution-authority-v1"

    def validate(self) -> None:
        if self.schema_version != "auren-execution-authority-v1":
            raise ValueError("unsupported execution authority schema")
        for name in ("execution_id", "run_id", "task_id", "repository_digest",
                     "repository_revision", "principal", "budget_id"):
            _required(getattr(self, name), name)
        for name, values in (
            ("allowed_capabilities", self.allowed_capabilities),
            ("allowed_actions", self.allowed_actions),
            ("allowed_targets", self.allowed_targets),
        ):
            if not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.expires_at is not None and (
            isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float))
        ):
            raise ValueError("expires_at must be a timestamp")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "repository_digest": self.repository_digest,
            "repository_revision": self.repository_revision,
            "principal": self.principal,
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "allowed_actions": sorted(self.allowed_actions),
            "allowed_targets": sorted(_normal_path(p) for p in self.allowed_targets),
            "budget_id": self.budget_id,
            "max_attempts": self.max_attempts,
            "expires_at": self.expires_at,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def authorize(
        self,
        request: Any,
        *,
        repository_revision: str,
        attempts_used: int = 0,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Check a request before it reaches the action gateway or executor."""
        self.validate()
        for attr in ("run_id", "actor", "capability", "action", "target"):
            if not hasattr(request, attr):
                return False, f"request_field_missing:{attr}"
        if request.run_id != self.run_id:
            return False, "run_identity_mismatch"
        if request.actor != self.principal:
            return False, "principal_mismatch"
        if repository_revision != self.repository_revision:
            return False, "repository_revision_mismatch"
        if request.capability not in self.allowed_capabilities:
            return False, "capability_out_of_scope"
        if request.action not in self.allowed_actions:
            return False, "action_out_of_scope"
        target = _normal_path(request.target)
        allowed = any(
            target == root or target.startswith(root.rstrip("/") + "/")
            for root in (_normal_path(p) for p in self.allowed_targets)
        )
        if not allowed:
            return False, "target_out_of_scope"
        if type(attempts_used) is not int or attempts_used < 0:
            return False, "invalid_attempt_count"
        if attempts_used >= self.max_attempts:
            return False, "attempt_budget_exhausted"
        current_time = time.time() if now is None else now
        if self.expires_at is not None and current_time >= self.expires_at:
            return False, "execution_authority_expired"
        return True, "execution_authority_valid"


@dataclass(frozen=True)
class GuardedExecutionCheckpoint:
    """Integrity-bound runtime guard snapshot associated with an authority digest.

    The checkpoint digest detects accidental/tampered payload changes. Replay
    protection still requires a trusted external store to persist the latest
    accepted sequence number.
    """

    authority_digest: str
    repository_revision: str
    sequence: int
    guard_snapshot: Mapping[str, Any]
    previous_checkpoint_digest: str = ""
    schema_version: str = "auren-guarded-checkpoint-v1"

    def payload(self) -> dict[str, Any]:
        if self.schema_version != "auren-guarded-checkpoint-v1":
            raise ValueError("unsupported guarded checkpoint schema")
        _required(self.authority_digest, "authority_digest")
        _required(self.repository_revision, "repository_revision")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("checkpoint sequence must be a non-negative integer")
        if not isinstance(self.guard_snapshot, Mapping):
            raise ValueError("guard_snapshot must be a mapping")
        return {
            "schema_version": self.schema_version,
            "authority_digest": self.authority_digest,
            "repository_revision": self.repository_revision,
            "sequence": self.sequence,
            "guard_snapshot": dict(self.guard_snapshot),
            "previous_checkpoint_digest": self.previous_checkpoint_digest,
        }

    @property
    def digest(self) -> str:
        return _digest(self.payload())

    @classmethod
    def capture(
        cls,
        authority: ExecutionAuthorityBinding,
        guard: RuntimeExecutionGuard,
        *,
        sequence: int,
        previous_checkpoint_digest: str = "",
    ) -> "GuardedExecutionCheckpoint":
        authority.validate()
        checkpoint = cls(
            authority_digest=authority.digest,
            repository_revision=authority.repository_revision,
            sequence=sequence,
            guard_snapshot=guard.snapshot(),
            previous_checkpoint_digest=previous_checkpoint_digest,
        )
        checkpoint.payload()
        return checkpoint

    def restore(
        self,
        authority: ExecutionAuthorityBinding,
        *,
        expected_repository_revision: str,
        minimum_sequence: int = 0,
    ) -> RuntimeExecutionGuard:
        payload = self.payload()
        if self.authority_digest != authority.digest:
            raise ValueError("checkpoint authority binding mismatch")
        if self.repository_revision != expected_repository_revision or expected_repository_revision != authority.repository_revision:
            raise ValueError("checkpoint repository revision mismatch")
        if self.sequence < minimum_sequence:
            raise ValueError("checkpoint replay or sequence rollback detected")
        # RuntimeExecutionGuard.resume validates its own snapshot digest and bounds.
        return RuntimeExecutionGuard.resume(payload["guard_snapshot"])


__all__ = [
    "EXECUTION_SURFACE_INVENTORY",
    "ExecutionAuthorityBinding",
    "ExecutionSurface",
    "GuardedExecutionCheckpoint",
    "execution_surface_audit",
]
