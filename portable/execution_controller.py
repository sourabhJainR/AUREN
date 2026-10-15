"""Durable default execution controller for AUREN.

This is the process boundary between graph/team execution and long-lived work.
Task handlers are registered by importable reference so a fresh service process
can reconstruct them after a restart. Handler state and outcomes live in SQLite.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import sqlite3
from threading import Event, Thread
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .autonomous_company import AutonomousCompany, TrustScore
from .canonical_evidence_ledger import CanonicalEvidenceLedger
from .decision_gate import require_valid_decision_record
from .engineering_recovery import verify_ci_gate
from .engineering_evidence_capture import EvidenceCapture

Handler = Callable[[Mapping[str, Any]], Any]

# Naming is a fail-closed safety net, not a substitute for operator-owned
# registration policy or repository branch protection.
_PROMOTION_HANDLER_TOKENS = frozenset({"promote", "promotion", "promoter", "merge", "merger", "release", "deploy", "deployment", "publish"})
_EXACT_HEAD_CI_HANDLER_TOKENS = frozenset({"merge", "merger", "release", "deploy", "deployment", "publish"})


def _handler_tokens(name: str) -> frozenset[str]:
    return frozenset(token for token in re.split(r"[^a-z0-9]+", name.lower()) if token)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _git_repository_identity(root: Path) -> tuple[str, str] | None:
    """Resolve a stable local repository slug and HEAD for evidence lineage."""
    import os
    import subprocess

    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            check=True, capture_output=True, text=True, timeout=2,
        ).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return None
    repository = os.environ.get("AUREN_REPOSITORY_SLUG", "").strip()
    if not repository:
        try:
            remote = subprocess.run(
                ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                check=True, capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            remote = ""
        match = re.search(r"(?:[:/])([^/:\s]+/[^/:\s]+?)(?:\.git)?$", remote)
        if match:
            repository = match.group(1)
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        return None
    return repository, head



class ProviderQuotaError(RuntimeError):
    """Provider adapter signal for a quota/rate-limit response.

    retry_after may be seconds or an absolute timezone-aware datetime. Set
    permanent=True for exhausted account quota requiring human intervention.
    """
    def __init__(self, message: str, *, provider: str = "unknown", retry_after: Any = None, permanent: bool = False):
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after
        self.permanent = permanent


@dataclass(frozen=True)
class DurableTask:
    id: str
    handler: str
    payload: dict[str, Any]
    state: str
    attempts: int
    work_unit_id: str
    lease_generation: int = 0
    lease_token: str | None = None


class LeaseLostError(RuntimeError):
    """Raised when a stale worker attempts to renew or finalize a task lease."""


class ExecutionController:
    """Durable supervisor used as the default owner of graph/team execution."""

    _LEASE_HEARTBEAT_INTERVAL_SECONDS = 30

    def __init__(
        self, root: str | Path, *, company: AutonomousCompany | None = None,
        evidence_signer: Callable[[bytes], Mapping[str, str]] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.company = company or AutonomousCompany(self.root)
        self.db_path = self.root / ".auren" / "execution.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, goal TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handlers(
              name TEXT PRIMARY KEY, reference TEXT NOT NULL, updated_at TEXT NOT NULL,
              ci_gate_policy_json TEXT NOT NULL DEFAULT '{}',
              promotion_gate_policy_json TEXT NOT NULL DEFAULT '{}',
              execution_class TEXT NOT NULL DEFAULT 'unclassified',
              implementation_digest TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              work_unit_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              heartbeat_at TEXT, lease_until TEXT,
              lease_generation INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
              quota_provider TEXT, quota_reason TEXT, quota_attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT, quota_blocked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS graph_checkpoints(
              task_id TEXT PRIMARY KEY,
              envelope_json TEXT NOT NULL,
              envelope_digest TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            """)
            handler_columns = {row[1] for row in db.execute("PRAGMA table_info(handlers)").fetchall()}
            if "ci_gate_policy_json" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN ci_gate_policy_json TEXT NOT NULL DEFAULT '{}'")
            if "promotion_gate_policy_json" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN promotion_gate_policy_json TEXT NOT NULL DEFAULT '{}'")
            if "execution_class" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN execution_class TEXT NOT NULL DEFAULT 'unclassified'")
            if "implementation_digest" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN implementation_digest TEXT NOT NULL DEFAULT ''")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            for column in ("heartbeat_at", "lease_until"):
                if column not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
            if "lease_generation" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0")
            if "lease_token" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN lease_token TEXT")
            # Additive migration preserves existing task and checkpoint data.
            for column, declaration in (
                ("quota_provider", "TEXT"), ("quota_reason", "TEXT"),
                ("quota_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("next_retry_at", "TEXT"), ("quota_blocked", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {declaration}")
        self.evidence_ledger = CanonicalEvidenceLedger(
            self.root / ".auren" / "canonical-evidence.sqlite3"
        )
        self.evidence_capture = EvidenceCapture.from_environment(
            self.root / ".auren" / "engineering-evidence.sqlite3",
            evidence_ledger=self.evidence_ledger,
            signer=evidence_signer,
        )

    def register_handler(
        self,
        name: str,
        handler: Handler | str,
        *,
        ci_gate_policy: Mapping[str, Any] | None = None,
        promotion_gate_policy: Mapping[str, Any] | None = None,
        execution_class: str | None = None,
    ) -> str:
        """Register a durable handler and trusted, operator-owned gate policies.

        Execution class is an operator-owned registration contract, persisted
        separately from task payload. New handlers must explicitly be classified
        as read_only, context_provider, ci_gated, or consequential. Unknown legacy
        registrations fail closed until reclassified. Consequential handlers
        require both trusted decision-record and exact-head CI gates. Context
        providers are internal-only and cannot be submitted as tasks.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("handler name is required")
        if isinstance(handler, str):
            reference = handler
        else:
            module = getattr(handler, "__module__", None)
            qualname = getattr(handler, "__qualname__", None)
            if not module or not qualname or "<locals>" in qualname:
                raise ValueError("durable handlers must be importable module-level callables")
            reference = f"{module}:{qualname}"

        resolved_handler = self._resolve_reference(reference) if isinstance(handler, str) else handler
        if not callable(resolved_handler):
            raise TypeError(f"durable handler is not callable: {reference}")
        implementation_digest = self._implementation_digest(resolved_handler)

        name_tokens = _handler_tokens(name)
        if isinstance(handler, str):
            handler_identity = handler.split(":", 1)[-1]
        else:
            handler_identity = str(getattr(handler, "__qualname__", ""))
        identity_tokens = _handler_tokens(handler_identity)
        tokens = name_tokens | identity_tokens
        explicit_action_tokens = {"promote", "promotion", "promoter", "merge", "merger", "release", "deploy", "deployment", "publish"}
        provider_action_exceptions = {"merge", "merger", "release", "deploy", "deployment", "publish", "promote", "promoter"}

        with sqlite3.connect(self.db_path) as db:
            prior_registration = db.execute(
                "SELECT reference, execution_class, implementation_digest FROM handlers WHERE name=?", (name,)
            ).fetchone()
        prior_reference = str(prior_registration[0]) if prior_registration else None
        prior_class = str(prior_registration[1]) if prior_registration else None
        prior_digest = str(prior_registration[2]) if prior_registration else ""
        if execution_class is None and prior_class is not None:
            execution_class = prior_class
        if prior_class is not None and prior_class != "unclassified" and prior_reference != reference:
            raise ValueError(
                "registered handler reference is immutable; use a new handler identity for a different implementation"
            )
        if prior_class is not None and prior_class != "unclassified" and not prior_digest:
            raise ValueError(
                "classified legacy handler has no implementation digest; migrate it under a new handler identity"
            )
        if prior_class is not None and prior_class != "unclassified" and prior_digest != implementation_digest:
            raise ValueError(
                "registered handler implementation digest is immutable; use a new handler identity for changed code"
            )
        if (
            prior_class is not None
            and prior_class != "unclassified"
            and execution_class != prior_class
        ):
            raise ValueError(
                "registered execution_class is immutable; use a new handler identity for a different effect class"
            )
        if execution_class not in {"read_only", "context_provider", "ci_gated", "consequential"}:
            raise ValueError(
                "handler registration requires an explicit execution_class: "
                "read_only, context_provider, ci_gated, or consequential"
            )
        is_trusted_provider_identity = (
            execution_class == "context_provider"
            and bool(tokens & {"context", "provider", "evidence", "validator", "verifier"})
            and not bool(tokens & provider_action_exceptions)
        )
        if tokens & explicit_action_tokens and execution_class != "consequential" and not is_trusted_provider_identity:
            raise ValueError("promotion/merge/release/deploy/publish handlers must be classified consequential")
        if execution_class == "context_provider" and tokens & provider_action_exceptions:
            raise ValueError("context providers cannot be consequential action handlers")
        if execution_class == "context_provider" and not is_trusted_provider_identity:
            raise ValueError(
                "context_provider handlers must have a context/evidence/provider/validator/verifier identity"
            )
        requires_promotion_gate = execution_class == "consequential"
        requires_exact_head_ci = execution_class in {"ci_gated", "consequential"}
        if requires_promotion_gate and not promotion_gate_policy:
            # A prior trusted policy is retained on re-registration below. For
            # a new handler, require the caller to declare the gate explicitly.
            with sqlite3.connect(self.db_path) as db:
                prior = db.execute(
                    "SELECT promotion_gate_policy_json FROM handlers WHERE name=?", (name,)
                ).fetchone()
            if not prior or not json.loads(prior[0] or "{}"):
                raise ValueError(
                    f"consequential handler {name!r} requires an operator-owned promotion_gate_policy"
                )
        if requires_exact_head_ci and not ci_gate_policy:
            with sqlite3.connect(self.db_path) as db:
                prior = db.execute(
                    "SELECT ci_gate_policy_json FROM handlers WHERE name=?", (name,)
                ).fetchone()
            if not prior or not json.loads(prior[0] or "{}"):
                raise ValueError(
                    f"consequential handler {name!r} requires an exact-head ci_gate_policy"
                )

        ci_policy = dict(ci_gate_policy or {})
        if ci_policy:
            expected_sha = ci_policy.get("expected_head_sha")
            required_checks = ci_policy.get("required_checks")
            evidence_handler = ci_policy.get("evidence_handler")
            if not isinstance(expected_sha, str) or not expected_sha.strip():
                raise ValueError("CI gate policy requires a trusted expected_head_sha")
            if (not isinstance(required_checks, (list, tuple)) or not required_checks
                    or not all(isinstance(item, str) and item.strip() for item in required_checks)):
                raise ValueError("CI gate policy requires non-empty trusted required_checks")
            if not isinstance(evidence_handler, str) or not evidence_handler.strip():
                raise ValueError("CI gate policy requires a trusted evidence_handler")
            if evidence_handler == name:
                raise ValueError("CI evidence provider cannot be the protected handler itself")
            self._load_handler(evidence_handler)
            ci_policy = {
                "expected_head_sha": expected_sha,
                "required_checks": list(dict.fromkeys(required_checks)),
                "evidence_handler": evidence_handler,
            }

        promotion_policy = dict(promotion_gate_policy or {})
        if promotion_policy:
            context_handler = promotion_policy.get("context_handler")
            if not isinstance(context_handler, str) or not context_handler.strip():
                raise ValueError("promotion gate policy requires a trusted context_handler")
            if context_handler == name:
                raise ValueError("promotion context provider cannot be the protected handler itself")
            self._load_handler(context_handler)
            promotion_policy = {"context_handler": context_handler}

        with sqlite3.connect(self.db_path) as db:
            prior = db.execute(
                "SELECT ci_gate_policy_json,promotion_gate_policy_json FROM handlers WHERE name=?",
                (name,),
            ).fetchone()
            # Re-registering a handler after restart must not silently remove
            # either trusted gate when the caller omits or empties its policy.
            if not ci_policy and prior:
                ci_policy = json.loads(prior[0] or "{}")
            if not promotion_policy and prior:
                promotion_policy = json.loads(prior[1] or "{}")
            db.execute(
                "INSERT INTO handlers(name,reference,updated_at,ci_gate_policy_json,promotion_gate_policy_json,execution_class,implementation_digest) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "reference=excluded.reference,updated_at=excluded.updated_at,"
                "ci_gate_policy_json=excluded.ci_gate_policy_json,"
                "promotion_gate_policy_json=excluded.promotion_gate_policy_json,"
                "execution_class=excluded.execution_class,"
                "implementation_digest=excluded.implementation_digest",
                (name, reference, _now(), json.dumps(ci_policy, sort_keys=True),
                 json.dumps(promotion_policy, sort_keys=True), execution_class, implementation_digest),
            )
        return reference

    def _handler_execution_class(self, name: str) -> str:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT execution_class FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        value = row[0]
        return value if isinstance(value, str) else "unclassified"

    def _handler_ci_gate_policy(self, name: str) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT ci_gate_policy_json FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        policy = json.loads(row[0] or "{}")
        if not isinstance(policy, dict):
            raise ValueError(f"invalid persisted CI gate policy for handler: {name}")
        return policy

    def _handler_promotion_gate_policy(self, name: str) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT promotion_gate_policy_json FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        policy = json.loads(row[0] or "{}")
        if not isinstance(policy, dict):
            raise ValueError(f"invalid persisted promotion gate policy for handler: {name}")
        return policy

    @staticmethod
    def _resolve_reference(reference: str) -> Any:
        module_name, qualname = reference.split(":", 1)
        obj: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return obj

    @staticmethod
    def _implementation_digest(handler: Handler) -> str:
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError) as exc:
            raise ValueError(
                "durable handler implementation source cannot be fingerprinted"
            ) from exc
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _load_handler(self, name: str) -> Handler:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT reference, implementation_digest, execution_class FROM handlers WHERE name=?", (name,)
            ).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        reference, expected_digest, execution_class = str(row[0]), str(row[1] or ""), str(row[2])
        obj = self._resolve_reference(reference)
        if not callable(obj):
            raise TypeError(f"durable handler is not callable: {reference}")
        if execution_class != "unclassified":
            if not expected_digest:
                raise ValueError(f"classified durable handler has no implementation digest: {name}")
            actual_digest = self._implementation_digest(obj)
            if actual_digest != expected_digest:
                raise ValueError(f"durable handler implementation digest mismatch: {name}")
        return obj

    def _preflight_gates(self, task: DurableTask) -> tuple[bool, str, tuple[str, ...]]:
        """Validate operator-owned handler class and required gates before side effects."""
        evidence: list[str] = []
        promotion_expected_head: str | None = None
        try:
            execution_class = self._handler_execution_class(task.handler)
            if execution_class == "context_provider":
                return False, "internal context providers cannot be executed as task handlers", ("handler-class:rejected",)
            if execution_class not in {"read_only", "ci_gated", "consequential"}:
                return False, "unclassified legacy handler fails closed", ("handler-class:unclassified",)
            configured_promotion_policy = self._handler_promotion_gate_policy(task.handler)
            configured_ci_policy = self._handler_ci_gate_policy(task.handler)
            if execution_class == "consequential" and (not configured_promotion_policy or not configured_ci_policy):
                return False, "consequential handler is missing mandatory promotion or exact-head CI policy", ("promotion-gate:missing", "ci-gate:missing")
            if execution_class == "ci_gated" and not configured_ci_policy:
                return False, "CI-gated handler is missing mandatory exact-head CI policy", ("ci-gate:missing",)
            promotion_policy = self._handler_promotion_gate_policy(task.handler)
            if promotion_policy and self.evidence_capture.sink is not None:
                pending_exports = self.evidence_capture.pending_exports()
                if pending_exports:
                    return False, (
                        f"promotion gate rejected task: {pending_exports} engineering evidence export(s) "
                        "are pending in the configured external retention sink"
                    ), ("evidence-export:pending", "promotion-gate:rejected")
            if promotion_policy:
                provider = self._load_handler(promotion_policy["context_handler"])
                # Do not pass task payload: the provider must resolve live repository/head
                # and canonical evidence from trusted sources, not caller-controlled input.
                context = provider({
                    "task_id": task.id,
                    "handler": task.handler,
                    "work_unit_id": task.work_unit_id,
                })
                if not isinstance(context, Mapping):
                    return False, "trusted promotion context provider returned no valid context", ("promotion-gate:missing",)
                record = context.get("decision_record")
                expected_repository = context.get("expected_repository")
                expected_head = context.get("expected_head")
                promotion_expected_head = expected_head if isinstance(expected_head, str) else None
                approvers = context.get("authorized_approvers")
                if not isinstance(record, Mapping):
                    return False, "promotion gate rejected task: trusted decision record is missing", ("promotion-gate:rejected",)
                promotion = record.get("promotion")
                promotion_level = promotion.get("level") if isinstance(promotion, Mapping) else None
                if execution_class == "consequential" and promotion_level not in {
                    "verified_for_scope", "repeatedly_reliable", "independently_corroborated"
                }:
                    return False, (
                        "consequential handler requires at least verified_for_scope promotion evidence"
                    ), ("promotion-gate:rejected",)
                if isinstance(approvers, str) or not isinstance(approvers, (list, tuple, set, frozenset)):
                    return False, "promotion gate rejected task: authorized approver context is malformed", ("promotion-gate:rejected",)
                if not isinstance(expected_repository, str) or not expected_repository.strip():
                    return False, "promotion gate rejected task: trusted repository context is missing", ("promotion-gate:rejected",)
                if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head):
                    return False, "promotion gate rejected task: trusted candidate head is invalid", ("promotion-gate:rejected",)
                # Resolve evidence from the controller-owned canonical ledger. A provider
                # or record-supplied list must never self-authorize cited evidence IDs.
                evidence_ids = self.evidence_ledger.known_evidence_ids(
                    repository=expected_repository, head_sha=expected_head,
                )
                require_valid_decision_record(
                    record,
                    expected_repository=expected_repository,
                    expected_head=expected_head,
                    authorized_approvers=approvers,
                    known_evidence_ids=evidence_ids,
                )
                evidence.extend((f"decision-record-head:{expected_head}", "promotion-gate:decision-record-passed"))

            ci_policy = self._handler_ci_gate_policy(task.handler)
            if ci_policy:
                if promotion_policy and ci_policy["expected_head_sha"] != promotion_expected_head:
                    return False, "exact-head CI policy does not match the live promotion candidate", tuple((*evidence, "ci-gate:rejected"))
                provider = self._load_handler(ci_policy["evidence_handler"])
                gate = provider({
                    "expected_head_sha": ci_policy["expected_head_sha"],
                    "required_checks": list(ci_policy["required_checks"]),
                })
                if not isinstance(gate, Mapping):
                    return False, "trusted CI evidence provider returned no valid evidence", tuple((*evidence, "ci-gate:missing"))
                passed_checks = gate.get("passed_checks", ())
                skipped_checks = gate.get("skipped_checks", ())
                if not all(
                    isinstance(items, (list, tuple)) and all(isinstance(item, str) and item.strip() for item in items)
                    for items in (passed_checks, skipped_checks)
                ):
                    return False, "malformed exact-head CI evidence: check lists must be arrays of strings", tuple((*evidence, "ci-gate:rejected"))
                verdict = verify_ci_gate(
                    expected_head_sha=ci_policy["expected_head_sha"],
                    ci_head_sha=str(gate.get("head_sha", "")),
                    status=str(gate.get("status", "")),
                    conclusion=str(gate.get("conclusion", "")) or None,
                    required_checks=ci_policy["required_checks"],
                    passed_checks=passed_checks,
                    skipped_checks=skipped_checks,
                )
                if not verdict.accepted:
                    return False, f"exact-head CI gate rejected task: {verdict.reason}", tuple((*evidence, "ci-gate:rejected"))
                evidence.extend((f"ci-head:{gate.get('head_sha')}", "ci-gate:required-checks-passed"))
            return True, "preflight gates passed", tuple(evidence)
        except Exception as exc:
            # The gate fails closed before the task handler can cause side effects.
            return False, f"preflight gate rejected task: {type(exc).__name__}: {exc}", tuple((*evidence, "preflight-gate:rejected"))

    def _capture_event(
        self, event_type: str, task: DurableTask, *, status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record safe lifecycle metadata without serializing task payload or output."""
        import subprocess
        head_sha = ""
        try:
            head_sha = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--verify", "HEAD^{commit}"],
                check=True, capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        repository = __import__("os").environ.get("AUREN_REPOSITORY_SLUG", "")
        self.evidence_capture.record(
            event_type,
            work_unit_id=task.work_unit_id,
            task_id=task.id,
            iteration=task.attempts,
            status=status,
            repository=repository,
            head_sha=head_sha,
            metadata=dict(metadata or {}),
        )

    def work_unit_for_session(self, session_id: str, goal: str) -> str:
        """Return a stable work unit for a session, creating it once if needed."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT work_unit_id, goal FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row:
                prior_state, _ = self.company._state(str(row[0]))
                if prior_state in {"running", "waiting_ci", "waiting_external"}:
                    if row[1] != goal:
                        raise ValueError("session_id is already bound to a different active goal")
                    return str(row[0])
                work = self.company.start(goal)
                db.execute("UPDATE sessions SET work_unit_id=?,goal=?,updated_at=? WHERE session_id=?",
                           (work.id, goal, _now(), session_id))
                return work.id
            work = self.company.start(goal)
            db.execute(
                "INSERT INTO sessions(session_id,work_unit_id,goal,updated_at) VALUES(?,?,?,?)",
                (session_id, work.id, goal, _now()),
            )
        return work.id

    def submit(self, handler: str, payload: Mapping[str, Any], *, goal: str | None = None) -> DurableTask:
        execution_class = self._handler_execution_class(handler)
        if execution_class == "context_provider":
            raise ValueError("context providers are internal-only and cannot be submitted as tasks")
        if execution_class not in {"read_only", "ci_gated", "consequential"}:
            raise ValueError("unclassified legacy handler fails closed; re-register with an explicit execution_class")
        self._load_handler(handler)
        work = self.company.start(goal or f"execute durable handler: {handler}")
        task = DurableTask(uuid4().hex, handler, dict(payload), "pending", 0, work.id)
        with sqlite3.connect(self.db_path) as db:
            now = _now()
            db.execute(
                "INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task.id, task.handler, json.dumps(task.payload, sort_keys=True),
                 task.state, task.attempts, task.work_unit_id, now, now, now, now),
            )
        self._capture_event("task_submitted", task, status="pending", metadata={"handler": task.handler})
        return task

    def _claim(self, task_id: str) -> DurableTask | None:
        # Claim under a write lock so two controllers cannot both observe
        # "pending" and execute the same side-effecting handler. Each claim
        # increments a fencing generation and receives a fresh unguessable token.
        with sqlite3.connect(self.db_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id,lease_until,lease_generation "
                "FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not row or row[3] in {"completed", "failed", "cancelled"}:
                return None
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            if row[3] == "running":
                # A live lease belongs to another worker. Missing lease metadata
                # is ambiguous and must be recovered explicitly, not stolen.
                if not row[6]:
                    return None
                try:
                    current_lease = datetime.fromisoformat(str(row[6]))
                except ValueError:
                    return None
                if current_lease.tzinfo is None:
                    return None
                if current_lease > now_dt:
                    return None
            attempts = int(row[4]) + 1
            generation = int(row[7] or 0) + 1
            token = uuid4().hex
            lease = (now_dt + timedelta(minutes=5)).isoformat()
            cursor = db.execute(
                "UPDATE tasks SET state='running',attempts=?,updated_at=?,heartbeat_at=?,lease_until=?,"
                "lease_generation=?,lease_token=? WHERE id=? AND state IN ('pending','running') AND lease_generation=?",
                (attempts, now, now, lease, generation, token, task_id, int(row[7] or 0)),
            )
            if cursor.rowcount != 1:
                return None
        return DurableTask(
            row[0], row[1], json.loads(row[2]), "running", attempts, row[5],
            lease_generation=generation, lease_token=token,
        )

    def _finalize_claim(self, task: DurableTask, state: str) -> bool:
        if state not in {"completed", "failed", "cancelled"}:
            raise ValueError("task final state is invalid")
        if not task.lease_token or task.lease_generation < 1:
            raise LeaseLostError("task claim has no fencing token")
        now = _now()
        with sqlite3.connect(self.db_path, timeout=10) as db:
            cursor = db.execute(
                "UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL,lease_token=NULL,"
                "quota_attempts=CASE WHEN ?='completed' THEN 0 ELSE quota_attempts END,"
                "quota_reason=CASE WHEN ?='completed' THEN NULL ELSE quota_reason END,"
                "quota_blocked=CASE WHEN ?='completed' THEN 0 ELSE quota_blocked END "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (state, now, now, state, state, state, task.id, task.lease_token, task.lease_generation),
            )
        return cursor.rowcount == 1

    def _invoke_with_lease_heartbeat(self, task: DurableTask, handler: Handler) -> Any:
        """Renew the current lease while a handler runs; reject results after lease loss."""
        stop = Event()
        lease_errors: list[BaseException] = []

        def renew() -> None:
            while not stop.wait(self._LEASE_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self.heartbeat(task)
                except BaseException as exc:
                    lease_errors.append(exc)
                    stop.set()
                    return

        thread = Thread(target=renew, name=f"auren-lease-{task.id[:8]}", daemon=True)
        thread.start()
        result: Any = None
        handler_error: BaseException | None = None
        try:
            result = handler(task.payload)
        except BaseException as exc:
            handler_error = exc
        finally:
            stop.set()
            thread.join(timeout=2)
        if lease_errors:
            raise LeaseLostError(
                "task lease was lost while handler was running; result cannot be committed"
            ) from (handler_error or lease_errors[0])
        if handler_error is not None:
            raise handler_error
        # Verify ownership once more before the caller can finalize the result.
        self.heartbeat(task)
        return result

    @staticmethod
    def _retry_after_datetime(value: Any, now: datetime) -> datetime | None:
        """Parse Retry-After seconds or an absolute date; reject ambiguous values."""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return None
            return max(now, value.astimezone(timezone.utc))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return now + timedelta(seconds=max(0, float(value)))
            except (OverflowError, ValueError):
                return None
        if isinstance(value, str) and value.strip():
            try:
                return now + timedelta(seconds=max(0, float(value.strip())))
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        parsed = parsedate_to_datetime(value.strip())
                    except (TypeError, ValueError, OverflowError):
                        return None
                if parsed.tzinfo is None:
                    return None
                return max(now, parsed.astimezone(timezone.utc))
        return None

    @staticmethod
    def _quota_signal(exc: Exception) -> tuple[bool, str, Any, bool]:
        """Return quota classification from a typed signal or HTTP-like error."""
        if isinstance(exc, ProviderQuotaError):
            return True, str(exc.provider or "unknown"), exc.retry_after, bool(exc.permanent)
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        headers = getattr(exc, "headers", None) or getattr(getattr(exc, "response", None), "headers", None) or {}
        if status in (402, "402"):
            return True, str(getattr(exc, "provider", "unknown")), None, True
        if status in (429, "429"):
            retry_after = (headers.get("Retry-After") or headers.get("retry-after")) if hasattr(headers, "get") else None
            if retry_after is None and hasattr(headers, "get"):
                # Common provider reset headers carry either an ISO date or Unix epoch.
                for key in (
                    "x-ratelimit-reset", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
                    "anthropic-ratelimit-requests-reset", "anthropic-ratelimit-tokens-reset",
                ):
                    reset = headers.get(key)
                    if not reset:
                        continue
                    try:
                        epoch = float(reset)
                    except (TypeError, ValueError):
                        retry_after = reset
                    else:
                        if epoch >= 1_000_000_000:
                            try:
                                retry_after = datetime.fromtimestamp(epoch, tz=timezone.utc)
                            except (OverflowError, OSError, ValueError):
                                retry_after = None
                        else:
                            retry_after = reset
                    break
            return True, str(getattr(exc, "provider", "unknown")), retry_after, False
        return False, "", None, False

    def _pause_for_quota(self, task: DurableTask, exc: Exception) -> bool:
        """Release a task lease into a durable, token-free wait state."""
        now_dt = datetime.now(timezone.utc)
        is_quota, provider, retry_after, permanent = self._quota_signal(exc)
        if not is_quota or not task.lease_token or task.lease_generation < 1:
            return False
        with sqlite3.connect(self.db_path, timeout=10) as db:
            row = db.execute(
                "SELECT quota_attempts FROM tasks WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (task.id, task.lease_token, task.lease_generation),
            ).fetchone()
            if not row:
                raise LeaseLostError("stale worker cannot park a task after quota exhaustion")
            quota_attempts = int(row[0] or 0) + 1
            retry_at = self._retry_after_datetime(retry_after, now_dt)
            # Permanent account quota or repeated unhinted 429s require intervention.
            blocked = permanent or (retry_at is None and quota_attempts >= 5)
            if retry_at is None and not blocked:
                retry_at = now_dt + timedelta(seconds=min(21600, 60 * (2 ** (quota_attempts - 1))))
            state = "blocked_quota" if blocked else "waiting_quota"
            # Provider exception text can contain request details or credentials;
            # persist a bounded category, not raw provider payloads.
            detail = f"{type(exc).__name__}: provider quota/rate-limit response; automatic retry is bounded"
            db.execute(
                "UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL,lease_token=NULL,"
                "quota_provider=?,quota_reason=?,quota_attempts=?,next_retry_at=?,quota_blocked=? "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (state, now_dt.isoformat(), now_dt.isoformat(), provider[:100], detail,
                 quota_attempts, retry_at.isoformat() if retry_at and not blocked else None,
                 int(blocked), task.id, task.lease_token, task.lease_generation),
            )
        self._capture_event("task_quota_wait" if not blocked else "task_quota_blocked", task,
                            status=state, metadata={"provider": provider[:100], "quota_attempts": quota_attempts,
                            "next_retry_at": retry_at.isoformat() if retry_at and not blocked else None})
        self.company.record(task.work_unit_id, "execution", state, detail,
                            (f"handler:{task.handler}", f"provider:{provider[:100]}"))
        self.company._transition(task.work_unit_id, "waiting_external")
        return True

    def due_quota_task_ids(self, *, now: datetime | None = None, limit: int = 20) -> list[str]:
        """Return only due waits; local SQLite reads do not contact providers."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with sqlite3.connect(self.db_path, timeout=10) as db:
            rows = db.execute(
                "SELECT id FROM tasks WHERE state='waiting_quota' AND next_retry_at IS NOT NULL "
                "AND next_retry_at<=? ORDER BY next_retry_at,created_at LIMIT ?",
                (current.astimezone(timezone.utc).isoformat(), limit),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def quota_status(self, task_id: str) -> dict[str, Any] | None:
        """Expose the durable wait/block reason without leaking provider exception text."""
        with sqlite3.connect(self.db_path, timeout=10) as db:
            row = db.execute(
                "SELECT state,quota_provider,quota_reason,quota_attempts,next_retry_at,quota_blocked "
                "FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "state": str(row[0]), "provider": str(row[1] or "unknown"),
            "reason": str(row[2] or ""), "attempts": int(row[3] or 0),
            "next_retry_at": row[4], "operator_action_required": bool(row[5]),
        }

    def seconds_until_quota_due(self, *, now: datetime | None = None) -> float | None:
        """Return the delay to the nearest quota wake; None means no scheduled quota wait."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with sqlite3.connect(self.db_path, timeout=10) as db:
            row = db.execute(
                "SELECT MIN(next_retry_at) FROM tasks WHERE state='waiting_quota' AND next_retry_at IS NOT NULL"
            ).fetchone()
        if not row or not row[0]:
            return None
        due = datetime.fromisoformat(str(row[0]))
        if due.tzinfo is None:
            return None
        return max(0.0, (due.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds())

    def wake_due_quota_tasks(self, *, now: datetime | None = None, limit: int = 20) -> list[str]:
        """Make due waits eligible for the existing supervisor; never calls a model."""
        ids = self.due_quota_task_ids(now=now, limit=limit)
        if not ids:
            return []
        current = now or datetime.now(timezone.utc)
        stamp = current.astimezone(timezone.utc).isoformat()
        work_units: list[str] = []
        woken_ids: list[str] = []
        with sqlite3.connect(self.db_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            for task_id in ids:
                row = db.execute(
                    "SELECT work_unit_id FROM tasks WHERE id=? AND state='waiting_quota' "
                    "AND next_retry_at IS NOT NULL AND next_retry_at<=?", (task_id, stamp),
                ).fetchone()
                if not row:
                    continue
                cursor = db.execute(
                    "UPDATE tasks SET state='pending',updated_at=?,next_retry_at=NULL,quota_reason=NULL "
                    "WHERE id=? AND state='waiting_quota' AND next_retry_at IS NOT NULL AND next_retry_at<=?",
                    (stamp, task_id, stamp),
                )
                if cursor.rowcount == 1:
                    work_units.append(str(row[0]))
                    woken_ids.append(task_id)
        for work_unit_id in work_units:
            self.company._transition(work_unit_id, "running")
        return woken_ids

    def unblock_quota_task(self, task_id: str, *, operator_reason: str) -> bool:
        """Explicitly release a permanently blocked quota task after operator action."""
        if not isinstance(operator_reason, str) or not operator_reason.strip():
            raise ValueError("operator_reason is required to unblock quota work")
        now = _now()
        with sqlite3.connect(self.db_path, timeout=10) as db:
            row = db.execute(
                "SELECT work_unit_id FROM tasks WHERE id=? AND state='blocked_quota'", (task_id,),
            ).fetchone()
            if not row:
                return False
            cursor = db.execute(
                "UPDATE tasks SET state='pending',updated_at=?,next_retry_at=NULL,quota_reason=NULL,"
                "quota_blocked=0,quota_attempts=0 WHERE id=? AND state='blocked_quota'", (now, task_id),
            )
        if cursor.rowcount != 1:
            return False
        self.company.record(str(row[0]), "execution", "operator_unblocked", operator_reason.strip(),
                            (f"task:{task_id}", "operator-override:quota"))
        self.company._transition(str(row[0]), "running")
        return True

    def run_once(self, task_id: str) -> TrustScore | None:
        task = self._claim(task_id)
        if task is None:
            return None
        self._capture_event("task_attempt_started", task, status="running", metadata={
            "handler": task.handler,
        })
        try:
            self.heartbeat(task)
            gates_passed, gate_detail, gate_evidence = self._preflight_gates(task)
            if not gates_passed:
                if not self._finalize_claim(task, "failed"):
                    raise LeaseLostError("stale worker cannot finalize rejected task")
                evidence = [f"handler:{task.handler}", *gate_evidence]
                self._capture_event("task_failed", task, status="failed", metadata={
                    "handler": task.handler, "phase": "preflight", "failure_class": "gate_rejection",
                })
                self.company.record(task.work_unit_id, "execution", "failed", gate_detail, evidence)
                return self.company.finish(task.work_unit_id, success=False, reason=gate_detail, evidence=evidence)

            handler = self._load_handler(task.handler)
            result = self._invoke_with_lease_heartbeat(task, handler)
        except LeaseLostError:
            raise
        except Exception as exc:
            if self._pause_for_quota(task, exc):
                return None
            if not self._finalize_claim(task, "failed"):
                raise LeaseLostError(
                    "task lease was lost; stale worker cannot overwrite the current task outcome"
                ) from exc
            detail = f"{type(exc).__name__}: {exc}"
            self._capture_event("task_failed", task, status="failed", metadata={
                "handler": task.handler, "phase": "exception",
                "failure_class": type(exc).__name__,
            })
            self.company.record(task.work_unit_id, "execution", "failed", detail, (f"handler:{task.handler}",))
            self.company.finish(task.work_unit_id, success=False, reason=detail)
            raise

        evidence = [f"handler:{task.handler}", *gate_evidence]
        if isinstance(result, Mapping):
            evidence.extend(str(x) for x in result.get("evidence", ()) if x)
            success_value = result.get("success", True)
            if type(success_value) is not bool:
                success = False
                detail = "handler success field must be a boolean"
            else:
                success = success_value
                detail = str(result.get("detail", "handler completed"))
        else:
            success, detail = True, "handler completed"

        state = "completed" if success else "failed"
        if not self._finalize_claim(task, state):
            raise LeaseLostError("task lease was lost; stale worker cannot finalize task outcome")
        self._capture_event(
            "task_completed" if success else "task_failed",
            task,
            status=state,
            metadata={
                "handler": task.handler, "phase": "handler",
                "result_category": "success" if success else "failure",
            },
        )
        self.company.record(task.work_unit_id, "execution", state, detail, evidence)
        return self.company.finish(task.work_unit_id, success=success, reason=detail, evidence=evidence)

    def resume_pending(self, *, limit: int = 20) -> list[TrustScore]:
        """Resume eligible work without letting a live lease consume the scan budget."""
        if type(limit) is not int or limit < 1:
            raise ValueError("resume limit must be a positive integer")
        self.wake_due_quota_tasks(limit=limit)
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id,state,lease_until FROM tasks "
                "WHERE state IN ('pending','running') ORDER BY updated_at",
            ).fetchall()

        # Apply the limit to eligible tasks, not merely the oldest rows. Otherwise
        # one live lease at the head of the queue can starve every pending task
        # whenever callers use a small recovery batch size. Missing, malformed,
        # or timezone-naive leases remain ambiguous and fail closed.
        task_ids: list[str] = []
        for task_id, state, lease_until in rows:
            if state == "pending":
                task_ids.append(str(task_id))
            elif state == "running" and lease_until:
                try:
                    lease_time = datetime.fromisoformat(str(lease_until))
                except ValueError:
                    continue
                if lease_time.tzinfo is None or lease_time > now:
                    continue
                task_ids.append(str(task_id))
            if len(task_ids) >= limit:
                break

        scores = []
        for task_id in task_ids:
            score = self.run_once(task_id)
            if score is not None:
                scores.append(score)
        return scores

    def heartbeat(self, task: DurableTask, *, lease_seconds: int = 300) -> None:
        if not isinstance(task, DurableTask) or not task.lease_token or task.lease_generation < 1:
            raise LeaseLostError("heartbeat requires the current durable task fencing token")
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=max(1, lease_seconds))).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as db:
            cursor = db.execute(
                "UPDATE tasks SET heartbeat_at=?,lease_until=?,updated_at=? "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (now.isoformat(), lease, now.isoformat(), task.id, task.lease_token, task.lease_generation),
            )
        if cursor.rowcount != 1:
            raise LeaseLostError("task lease was lost; stale worker cannot renew it")

    def detect_stale(self, stale_after_seconds: int = 900) -> list[DurableTask]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))).isoformat()
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks "
                "WHERE state='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)",
                (cutoff,),
            ).fetchall()
        return [DurableTask(r[0], r[1], json.loads(r[2]), r[3], int(r[4]), r[5]) for r in rows]

    def recover_stale(
        self, task_id: str, *, reason: str, stale_after_seconds: int = 900
    ) -> None:
        """Return a running task to pending only when its lease and heartbeat are stale."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))
        now_dt = datetime.now(timezone.utc)
        recovered: tuple[str, str] | None = None
        with sqlite3.connect(self.db_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,heartbeat_at,lease_until,work_unit_id FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not row or row[0] != "running":
                return
            heartbeat_at, lease_until = row[1], row[2]
            if lease_until:
                try:
                    lease_time = datetime.fromisoformat(str(lease_until))
                except ValueError:
                    return
                if lease_time.tzinfo is None or lease_time > now_dt:
                    return
            if heartbeat_at:
                try:
                    heartbeat_time = datetime.fromisoformat(str(heartbeat_at))
                except ValueError:
                    return
                if heartbeat_time.tzinfo is None or heartbeat_time > cutoff:
                    return
            cursor = db.execute(
                "UPDATE tasks SET state='pending',updated_at=?,lease_until=NULL "
                "WHERE id=? AND state='running'",
                (_now(), task_id),
            )
            if cursor.rowcount == 1:
                recovered = (task_id, str(row[3]))
        if recovered is not None:
            self.company.record(
                recovered[1], "recovery", "detected", reason, (f"task:{recovered[0]}",)
            )

    def generate_alternate_plans(self, task_id: str, reason: str) -> tuple[str, ...]:
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return (
            f"retry {task.handler} with verified inputs",
            f"reduce scope for {task.handler} and verify incrementally",
            f"switch to specialist review before {task.handler}",
            f"rollback to last verified checkpoint before {task.handler}",
        )

    def confirm_alternate(self, task_id: str, plan: str) -> tuple[bool, str, tuple[str, ...]]:
        task = self.task(task_id)
        if task is None:
            return False, "task not found", ()
        safe = not any(x in plan.lower() for x in ("disable security", "delete evidence", "expose secret"))
        return safe, "passed safety policy" if safe else "blocked by safety policy", (f"alternate:{task.id}", f"plan:{plan}")

    def self_review(self, *, limit: int = 20) -> int:
        count = 0
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id,work_unit_id,state,attempts FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        for task_id, wid, state, attempts in rows:
            ok = state != "failed" and attempts < 1000000
            self.company.record(
                wid, "self_review", "passed" if ok else "failed",
                f"task={task_id}; state={state}; attempts={attempts}", (f"task:{task_id}",),
            )
            count += 1
        return count

    def health_findings(self) -> tuple[str, ...]:
        findings = []
        with sqlite3.connect(self.db_path) as db:
            failed = db.execute("SELECT COUNT(*) FROM tasks WHERE state='failed'").fetchone()[0]
            stale = db.execute(
                "SELECT COUNT(*) FROM tasks WHERE state='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)",
                ((datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),),
            ).fetchone()[0]
        if failed:
            findings.append(f"failed_tasks={failed}")
        if stale:
            findings.append(f"stale_tasks={stale}")
        if self.evidence_capture.sink is not None:
            delivery = self.evidence_capture.flush_pending(limit=100)
            if delivery["pending"]:
                findings.append(f"evidence_exports_pending={delivery['pending']}")
        return tuple(findings)

    def task(self, task_id: str) -> DurableTask | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        return DurableTask(row[0], row[1], json.loads(row[2]), row[3], int(row[4]), row[5]) if row else None

    @staticmethod
    def _validate_graph_checkpoint_envelope(task_id: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Validate supervisor-owned crew checkpoint data before persistence or resume."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("graph checkpoint task_id is required")
        if not isinstance(envelope, Mapping):
            raise ValueError("graph checkpoint envelope must be a mapping")
        payload = dict(envelope)
        expected_fields = {
            "schema_version", "handoff_checkpoint", "retry_budget",
            "task_id", "intent_digest", "crew_plan_digest",
        }
        optional_fields = {"provider_resume_state", "runtime_guard", "repository", "head_sha"}
        if not expected_fields <= set(payload) or set(payload) - expected_fields - optional_fields:
            raise ValueError("supervisor handoff checkpoint envelope has missing or unknown fields")
        if ("repository" in payload) != ("head_sha" in payload):
            raise ValueError("checkpoint repository and head_sha must be supplied together")
        if "repository" in payload:
            repository = payload.get("repository")
            head_sha = payload.get("head_sha")
            if not isinstance(repository, str) or not repository.strip():
                raise ValueError("checkpoint repository must be non-empty text")
            if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
                raise ValueError("checkpoint head_sha must be a full lowercase commit SHA")
        if payload.get("schema_version") != "auren-task-handoff-envelope-v1":
            raise ValueError("unsupported supervisor handoff checkpoint envelope")
        if payload.get("task_id") != task_id:
            raise ValueError("graph checkpoint task_id does not match supervisor context")
        for key in ("intent_digest", "crew_plan_digest"):
            value = payload.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"graph checkpoint {key} must be a SHA-256 digest")
        checkpoint = payload.get("handoff_checkpoint")
        retry_snapshot = payload.get("retry_budget")
        if not isinstance(checkpoint, Mapping) or not isinstance(retry_snapshot, Mapping):
            raise ValueError("graph checkpoint requires handoff and retry-budget snapshots")
        # Reuse the canonical validators rather than trusting JSON shape alone.
        from .crew_adapters import GraphHandoffController, TaskRetryBudget
        budget = TaskRetryBudget.resume(retry_snapshot)
        embedded_budget = checkpoint.get("retry_budget")
        if not isinstance(embedded_budget, Mapping):
            raise ValueError("handoff checkpoint must embed the shared retry budget")
        embedded = TaskRetryBudget.resume(embedded_budget)
        if embedded.snapshot()["digest"] != budget.snapshot()["digest"]:
            raise ValueError("handoff and supervisor retry-budget checkpoints disagree")
        GraphHandoffController.resume(checkpoint, retry_budget=budget)
        runtime_guard = payload.get("runtime_guard")
        if runtime_guard is not None:
            if not isinstance(runtime_guard, Mapping):
                raise ValueError("runtime guard checkpoint must be a mapping")
            from .runtime_recovery import RuntimeExecutionGuard
            RuntimeExecutionGuard.resume(runtime_guard)
        provider_resume = payload.get("provider_resume_state")
        if provider_resume is not None:
            if not isinstance(provider_resume, Mapping):
                raise ValueError("provider resume state must be a mapping")
            provider_digest = provider_resume.get("digest")
            provider_payload = {key: value for key, value in provider_resume.items() if key != "digest"}
            canonical = json.dumps(
                provider_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            expected_provider_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if not isinstance(provider_digest, str) or provider_digest != expected_provider_digest:
                raise ValueError("provider resume checkpoint digest mismatch")
            if provider_resume.get("task_id") != task_id:
                raise ValueError("provider resume checkpoint task_id does not match supervisor context")
        return payload

    def save_graph_checkpoint(self, task_id: str, envelope: Mapping[str, Any]) -> None:
        """Atomically persist a digest-checked handoff envelope in the supervisor DB."""
        payload = self._validate_graph_checkpoint_envelope(task_id, envelope)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.db_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT envelope_json,envelope_digest FROM graph_checkpoints WHERE task_id=?", (task_id,)
            ).fetchone()
            if prior:
                old_encoded, old_digest = prior
                if hashlib.sha256(old_encoded.encode("utf-8")).hexdigest() != old_digest:
                    raise ValueError("stored graph checkpoint digest mismatch; explicit recovery is required")
                try:
                    old = json.loads(old_encoded)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("stored graph checkpoint is malformed; explicit recovery is required") from exc
                for key in ("intent_digest", "crew_plan_digest", "repository", "head_sha"):
                    if old.get(key) != payload.get(key):
                        raise ValueError(f"graph checkpoint {key} is immutable for a task")
            db.execute(
                "INSERT INTO graph_checkpoints(task_id,envelope_json,envelope_digest,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "envelope_json=excluded.envelope_json,envelope_digest=excluded.envelope_digest,"
                "updated_at=excluded.updated_at",
                (task_id, encoded, digest, _now()),
            )
        # Tie the durable state transition into the existing engineering evidence
        # journal. Deterministic event IDs make retries idempotent.
        event_id = (
            "crew-checkpoint:"
            + hashlib.sha256(task_id.encode("utf-8")).hexdigest()
            + ":"
            + digest
        )
        with sqlite3.connect(self.evidence_capture.path, timeout=10) as db:
            already_recorded = db.execute(
                "SELECT 1 FROM engineering_events WHERE event_id=?", (event_id,)
            ).fetchone()
        if not already_recorded:
            # Never attach a current repository identity to a legacy/unbound
            # checkpoint after the work has already happened.
            head_sha = payload.get("head_sha", "")
            repository = payload.get("repository", "")
            self.evidence_capture.record(
                "crew_handoff_checkpoint_persisted",
                work_unit_id=task_id,
                task_id=task_id,
                status="verified",
                repository=repository,
                head_sha=head_sha,
                artifact_digest=digest,
                evidence_uri=f"urn:auren:crew-checkpoint:{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}:{digest}",
                metadata={
                    "phase": "crew_handoff_checkpoint",
                    "checkpoint_digest": digest,
                    "crew_plan_digest": payload["crew_plan_digest"],
                    "retry_budget_digest": payload["retry_budget"]["digest"],
                    **({"provider_resume_digest": payload["provider_resume_state"]["digest"]}
                       if isinstance(payload.get("provider_resume_state"), Mapping) else {}),
                },
                event_id=event_id,
            )
        # The canonical ledger is the indexed, immutable evidence owner. This
        # local receipt proves checkpoint bytes/revision linkage only; its signer
        # label explicitly does not claim an independent cryptographic attestation.
        repository = payload.get("repository")
        head_sha = payload.get("head_sha")
        if repository and head_sha:
            self.evidence_ledger.record(
                evidence_id=event_id,
                repository=repository,
                head_sha=head_sha,
                evidence_type="crew_handoff_checkpoint",
                status="verified",
                source_uri=f"urn:auren:crew-checkpoint:{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}:{digest}",
                payload_digest=digest,
                signer_id="local-supervisor-unattested",
            )

    def load_graph_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Load a checkpoint only after validating its stored digest and task binding."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("graph checkpoint task_id is required")
        with sqlite3.connect(self.db_path, timeout=10) as db:
            row = db.execute(
                "SELECT envelope_json,envelope_digest FROM graph_checkpoints WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        encoded, supplied_digest = row
        actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if not isinstance(supplied_digest, str) or actual_digest != supplied_digest:
            raise ValueError("stored graph checkpoint digest mismatch")
        try:
            envelope = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("stored graph checkpoint is malformed") from exc
        return self._validate_graph_checkpoint_envelope(task_id, envelope)

    def trust(self, task_id: str) -> TrustScore:
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return self.company.trust(task.work_unit_id)

    def execute_graph(self, graph: Any, task_id: str, intent: str, context: Mapping[str, Any] | None = None) -> Any:
        """Execute graph work; consequential crew handoffs checkpoint in supervisor SQLite."""
        from .state_graph import StateGraph, GraphAgentTeam
        if isinstance(graph, GraphAgentTeam):
            state = dict(context or {})
            # Caller context cannot override or inject supervisor-owned checkpoint state.
            for reserved in (
                "auren_supervisor_checkpoint_envelope",
                "auren_crew_handoff_checkpoint_data",
                "auren_crew_handoff_checkpoint",
                "auren_retry_budget",
                "auren_provider_resume_state",
                "auren_provider_resume_persist",
            ):
                state.pop(reserved, None)
            saved = self.load_graph_checkpoint(task_id)
            if saved is not None:
                state["auren_supervisor_checkpoint_envelope"] = saved
            prior_writer = graph.handoff_checkpoint_writer
            # A legacy checkpoint without revision identity stays explicitly
            # unbound on resume; never retroactively attribute old work to today's HEAD.
            if saved is not None:
                identity = (
                    (saved["repository"], saved["head_sha"])
                    if "repository" in saved and "head_sha" in saved
                    else None
                )
            else:
                identity = _git_repository_identity(self.root)

            def persist_checkpoint(checkpoint_task_id: str, envelope: Mapping[str, Any]) -> None:
                if checkpoint_task_id != task_id:
                    raise ValueError("crew checkpoint task_id does not match active supervisor task")
                durable_envelope = dict(envelope)
                if identity is not None:
                    durable_envelope["repository"], durable_envelope["head_sha"] = identity
                self.save_graph_checkpoint(task_id, durable_envelope)
                if prior_writer is not None:
                    prior_writer(checkpoint_task_id, durable_envelope)

            # Pass supervisor authority per invocation; do not mutate a shared graph
            # object, which may be executing independent tasks concurrently.
            return graph.run(
                task_id,
                intent,
                state,
                handoff_checkpoint_writer=persist_checkpoint,
                require_durable_handoffs=True,
            )
        if isinstance(graph, StateGraph):
            return graph.run(task_id, intent, context)
        from .orchestration import Orchestrator
        return Orchestrator(graph).run(task_id, intent, context)


__all__ = ["ExecutionController", "DurableTask", "Handler", "ProviderQuotaError", "LeaseLostError"]
