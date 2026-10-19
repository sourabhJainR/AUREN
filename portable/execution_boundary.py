"""Execution-boundary inventory, authority binding, and safe resume contracts.

These contracts complement existing orchestration, ActionGovernanceGateway,
ExecutionEnvelope, and RuntimeExecutionGuard primitives. They do not replace
host authorization, trusted persistence, OS isolation, or downstream idempotency.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import posixpath
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .action_governance import ActionGovernanceGateway, ApprovalReceipt, GovernedResult, PolicyVerdict
from .runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard


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
    if (
        normalized in {".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ValueError("target path must be a repository-relative path within its declared root")
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
        "Policy verdicts, approval binding, external-effect idempotency, tamper-evident event chain, and opt-in authority-bound OpenShell, coding-agency, and specialist-team adapters",
        "Authority binding is not yet mandatory across every consequential caller; trace is not trusted storage",
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


# Reviewed direct ActionGovernanceGateway.execute call sites. The test suite
# compares this registry with an AST scan of portable/ so a new direct call
# cannot silently bypass review. Legacy adapter entries remain compatibility
# branches; authority-bound branches must continue to use execute_bound_action.
DIRECT_GATEWAY_CALLSITE_POLICY: tuple[dict[str, str], ...] = (
    {
        "path": "portable/execution_boundary.py",
        "scope": "execute_bound_action",
        "disposition": "canonical-after-authority-preflight",
    },
    {
        "path": "portable/governed_sandbox_execution.py",
        "scope": "execute_governed_sandbox",
        "disposition": "legacy branch requires durable ledger for external effects; bound mode uses execute_bound_action",
    },
    {
        "path": "portable/ai_coding_agency_bridge.py",
        "scope": "run_coding_task.effective_worker",
        "disposition": "legacy branch requires durable ledger for external effects; bound mode uses execute_bound_action",
    },
    {
        "path": "portable/agency_team_orchestrator.py",
        "scope": "run.effective_spawn",
        "disposition": "legacy branch requires durable ledger for external effects; bound mode uses execute_bound_action",
    },
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
        "direct_gateway_call_sites": [dict(item) for item in DIRECT_GATEWAY_CALLSITE_POLICY],
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
        for target in self.allowed_targets:
            _normal_path(target)
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.expires_at is not None and (
            isinstance(self.expires_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or not math.isfinite(self.expires_at)
        ):
            raise ValueError("expires_at must be a finite timestamp")

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
        repository_digest: str,
        attempts_used: int = 0,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Check a request before it reaches the action gateway or executor."""
        self.validate()
        for attr in ("run_id", "task_id", "actor", "capability", "action", "target"):
            if not hasattr(request, attr):
                return False, f"request_field_missing:{attr}"
        if request.run_id != self.run_id:
            return False, "run_identity_mismatch"
        if request.task_id != self.task_id:
            return False, "task_identity_mismatch"
        if request.actor != self.principal:
            return False, "principal_mismatch"
        if repository_revision != self.repository_revision:
            return False, "repository_revision_mismatch"
        if repository_digest != self.repository_digest:
            return False, "repository_digest_mismatch"
        if request.capability not in self.allowed_capabilities:
            return False, "capability_out_of_scope"
        if request.action not in self.allowed_actions:
            return False, "action_out_of_scope"
        try:
            target = _normal_path(request.target)
        except ValueError:
            return False, "invalid_target_path"
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
        if isinstance(current_time, bool) or not isinstance(current_time, (int, float)) or not math.isfinite(current_time):
            return False, "invalid_authority_check_time"
        if self.expires_at is not None and current_time >= self.expires_at:
            return False, "execution_authority_expired"
        return True, "execution_authority_valid"


def execute_bound_action(
    gateway: ActionGovernanceGateway,
    authority: ExecutionAuthorityBinding,
    request: Any,
    handler: Any,
    *,
    repository_revision: str,
    repository_digest: str,
    budget_id: str,
    execution_guard: RuntimeExecutionGuard,
    approval: ApprovalReceipt | None = None,
) -> GovernedResult:
    """Enforce execution binding before delegating to the canonical action gateway.

    This wrapper is the safe integration point for callers that have an authority
    binding. A binding denial is recorded as a policy verdict and the handler is
    never invoked. The gateway still makes its own allowlist/approval decision.
    """
    if not callable(handler):
        raise TypeError("handler must be callable")
    if not isinstance(execution_guard, RuntimeExecutionGuard):
        raise TypeError("execution_guard must be the task-owned RuntimeExecutionGuard")
    fingerprint_fn = getattr(request, "fingerprint", None)
    if not callable(fingerprint_fn):
        raise TypeError("request must expose fingerprint()")
    if budget_id != authority.budget_id:
        return GovernedResult(False, PolicyVerdict("deny", "budget_identity_mismatch", fingerprint_fn()))
    if execution_guard.max_attempts > authority.max_attempts:
        return GovernedResult(False, PolicyVerdict("deny", "budget_limit_exceeds_authority", fingerprint_fn()))
    allowed, reason = authority.authorize(
        request,
        repository_revision=repository_revision,
        repository_digest=repository_digest,
        attempts_used=execution_guard.attempts_used,
    )
    if not allowed:
        return GovernedResult(executed=False, verdict=PolicyVerdict(
            decision="deny", reason=reason, request_fingerprint=fingerprint_fn(),
        ))
    durable_verdict = gateway.durable_side_effect_preflight(request)
    if durable_verdict is not None:
        return GovernedResult(False, durable_verdict)
    try:
        charged = execution_guard.charge_attempt(f"{authority.budget_id}:{request.action_id}")
    except ExecutionBudgetExceeded:
        charged = False
    if not charged:
        return GovernedResult(False, PolicyVerdict("deny", "attempt_budget_exhausted_or_duplicate", fingerprint_fn()))
    return gateway.execute(request, handler, approval=approval)


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
    checkpoint_digest: str = ""

    def __post_init__(self) -> None:
        computed = _digest(self.payload())
        if self.checkpoint_digest and self.checkpoint_digest != computed:
            raise ValueError("checkpoint digest mismatch")
        if not self.checkpoint_digest:
            object.__setattr__(self, "checkpoint_digest", computed)

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
        computed = _digest(self.payload())
        if computed != self.checkpoint_digest:
            raise ValueError("checkpoint digest mismatch")
        return self.checkpoint_digest

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
        if not isinstance(guard, RuntimeExecutionGuard):
            raise TypeError("guard must be the task-owned RuntimeExecutionGuard")
        if guard.max_attempts > authority.max_attempts:
            raise ValueError("checkpoint attempt budget exceeds execution authority")
        checkpoint = cls(
            authority_digest=authority.digest,
            repository_revision=authority.repository_revision,
            sequence=sequence,
            guard_snapshot=guard.snapshot(),
            previous_checkpoint_digest=previous_checkpoint_digest,
        )
        checkpoint.payload()
        return checkpoint

    def to_dict(self) -> dict[str, Any]:
        value = self.payload()
        value["digest"] = self.digest
        return value

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GuardedExecutionCheckpoint":
        if not isinstance(raw, Mapping):
            raise TypeError("checkpoint must be a mapping")
        allowed_keys = {
            "schema_version", "authority_digest", "repository_revision", "sequence",
            "guard_snapshot", "previous_checkpoint_digest", "digest",
        }
        if set(raw) != allowed_keys:
            raise ValueError("checkpoint contains missing or unknown fields")
        supplied = raw.get("digest")
        payload = {key: value for key, value in raw.items() if key != "digest"}
        checkpoint = cls(
            authority_digest=str(payload.get("authority_digest", "")),
            repository_revision=str(payload.get("repository_revision", "")),
            sequence=payload.get("sequence"),
            guard_snapshot=payload.get("guard_snapshot", {}),
            previous_checkpoint_digest=str(payload.get("previous_checkpoint_digest", "")),
            schema_version=str(payload.get("schema_version", "")),
        )
        if not isinstance(supplied, str) or supplied != checkpoint.digest:
            raise ValueError("checkpoint digest mismatch")
        return checkpoint

    def restore(
        self,
        authority: ExecutionAuthorityBinding,
        *,
        expected_repository_revision: str,
        minimum_sequence: int = 0,
    ) -> RuntimeExecutionGuard:
        payload = self.payload()
        self.digest  # Revalidate nested checkpoint data against the captured digest.
        if self.authority_digest != authority.digest:
            raise ValueError("checkpoint authority binding mismatch")
        if self.repository_revision != expected_repository_revision or expected_repository_revision != authority.repository_revision:
            raise ValueError("checkpoint repository revision mismatch")
        if type(minimum_sequence) is not int or minimum_sequence < 0:
            raise ValueError("minimum_sequence must be a non-negative integer")
        if self.sequence < minimum_sequence:
            raise ValueError("checkpoint replay or sequence rollback detected")
        # Validate the nested budget before returning any resumed execution capability.
        restored = RuntimeExecutionGuard.resume(payload["guard_snapshot"])
        if restored.max_attempts > authority.max_attempts:
            raise ValueError("checkpoint attempt budget exceeds execution authority")
        if restored.attempts_used > authority.max_attempts:
            raise ValueError("checkpoint attempts used exceed execution authority")
        return restored


class ExecutionCheckpointConflict(RuntimeError):
    """Checkpoint save was stale, replayed, or linked to a different chain."""


class ExecutionCheckpointStore:
    """SQLite-backed latest checkpoint per authority with monotonic chain linkage.

    Use a host-owned database path with restrictive filesystem permissions.
    SQLite transactions serialize concurrent writers but do not defend against a
    privileged writer that can alter the database and recompute hashes.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("checkpoint database path must not be a symbolic link")
        with closing(self._connect()) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS execution_checkpoints (
                    authority_digest TEXT PRIMARY KEY,
                    repository_revision TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    checkpoint_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            # Some filesystems do not support POSIX permissions; host policy remains required.
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def save(
        self,
        checkpoint: GuardedExecutionCheckpoint,
        authority: ExecutionAuthorityBinding,
    ) -> str:
        authority.validate()
        if checkpoint.authority_digest != authority.digest:
            raise ExecutionCheckpointConflict("checkpoint authority binding mismatch")
        if checkpoint.repository_revision != authority.repository_revision:
            raise ExecutionCheckpointConflict("checkpoint repository revision mismatch")
        checkpoint.restore(
            authority,
            expected_repository_revision=authority.repository_revision,
            minimum_sequence=0,
        )
        checkpoint_digest = checkpoint.digest
        payload_json = json.dumps(
            checkpoint.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT sequence, checkpoint_digest, repository_revision "
                    "FROM execution_checkpoints WHERE authority_digest=?",
                    (authority.digest,),
                ).fetchone()
                if row is None:
                    if checkpoint.previous_checkpoint_digest:
                        raise ExecutionCheckpointConflict(
                            "first checkpoint must not reference a missing predecessor"
                        )
                else:
                    if row["repository_revision"] != checkpoint.repository_revision:
                        raise ExecutionCheckpointConflict(
                            "repository revision changed within checkpoint chain"
                        )
                    if checkpoint.sequence <= int(row["sequence"]):
                        raise ExecutionCheckpointConflict(
                            "checkpoint sequence must increase monotonically"
                        )
                    if checkpoint.previous_checkpoint_digest != row["checkpoint_digest"]:
                        raise ExecutionCheckpointConflict("checkpoint predecessor digest mismatch")
                db.execute(
                    """INSERT INTO execution_checkpoints
                       (authority_digest, repository_revision, sequence, checkpoint_digest, payload_json, updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(authority_digest) DO UPDATE SET
                         repository_revision=excluded.repository_revision,
                         sequence=excluded.sequence,
                         checkpoint_digest=excluded.checkpoint_digest,
                         payload_json=excluded.payload_json,
                         updated_at=excluded.updated_at""",
                    (
                        authority.digest, checkpoint.repository_revision, checkpoint.sequence,
                        checkpoint_digest, payload_json, time.time(),
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return checkpoint_digest

    def latest(self, authority: ExecutionAuthorityBinding) -> GuardedExecutionCheckpoint | None:
        authority.validate()
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT repository_revision, sequence, checkpoint_digest, payload_json "
                "FROM execution_checkpoints WHERE authority_digest=?",
                (authority.digest,),
            ).fetchone()
        if row is None:
            return None
        if row["repository_revision"] != authority.repository_revision:
            raise ExecutionCheckpointConflict("stored checkpoint repository revision mismatch")
        try:
            raw = json.loads(row["payload_json"])
            checkpoint = GuardedExecutionCheckpoint.from_dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExecutionCheckpointConflict("stored checkpoint payload is invalid") from exc
        if checkpoint.digest != row["checkpoint_digest"] or checkpoint.sequence != int(row["sequence"]):
            raise ExecutionCheckpointConflict("stored checkpoint row does not match its payload")
        if checkpoint.authority_digest != authority.digest:
            raise ExecutionCheckpointConflict("stored checkpoint authority mismatch")
        checkpoint.restore(
            authority,
            expected_repository_revision=authority.repository_revision,
            minimum_sequence=checkpoint.sequence,
        )
        return checkpoint

    def restore_latest(
        self,
        authority: ExecutionAuthorityBinding,
        *,
        expected_repository_revision: str,
    ) -> RuntimeExecutionGuard:
        checkpoint = self.latest(authority)
        if checkpoint is None:
            raise LookupError("no checkpoint exists for this execution authority")
        return checkpoint.restore(
            authority,
            expected_repository_revision=expected_repository_revision,
            minimum_sequence=checkpoint.sequence,
        )


__all__ = [
    "EXECUTION_SURFACE_INVENTORY",
    "ExecutionAuthorityBinding",
    "ExecutionCheckpointConflict",
    "ExecutionCheckpointStore",
    "ExecutionSurface",
    "GuardedExecutionCheckpoint",
    "execution_surface_audit",
    "execute_bound_action",
]
