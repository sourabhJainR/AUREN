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
import math
import re
import sqlite3
from contextlib import closing
import time
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
from .side_effect_ledger import DurableSideEffectLedger
from .evidence_gate import EvidenceCheck
from .evidence_gate_integration import EvidenceGateIntegration, IntegratedEvidenceReceipt
from .persistent_memory import PersistentMemory
from .progress_journal import ProgressJournal
from .provider_operation_lifecycle import ProviderOutcomeBlocked
from .durable_approval import ApprovalPending, InvalidApprovalTransition
from .workflow_profile import WorkflowProfile, delivery_mode_allows, infer_delivery_action, resolve_profile
from .workflow_profile_store import load_profile
from .organization_guard import POLICY_VERSION, GuardDecision, GuardPolicyError, evaluate_guard_policy, guard_policy_fingerprint, load_guard_policy
from .agent_execution_guard import agent_assignment_scope, validate_agent_assignment_for_dispatch
from .agent_org_runtime import load_agent_org_registry

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
    deadline_at: str | None = None


@dataclass(frozen=True)
class ContinuousRunReport:
    """Bounded continuous-service slice; scores are evidence summaries, not success claims."""

    cycles: int
    processed_work_units: int
    completed_tasks: int
    failed_tasks: int
    remaining_nonterminal_tasks: int
    idle_cycles: int
    stop_reason: str
    trust_scores: tuple[TrustScore, ...]
    task_state_counts: tuple[tuple[str, int], ...] = ()


class WorkKeyConflict(ValueError):
    """A stable work key was reused for a different task intent."""


class LeaseLostError(RuntimeError):
    """Raised when a stale worker attempts to renew or finalize a task lease."""


class TaskCancellationRequested(InterruptedError):
    """Raised when the supervisor requests cancellation of an active task."""


class TaskDeadlineExceeded(TimeoutError):
    """Raised when a durable task exceeds its supervisor-owned deadline."""


class ExecutionController:
    """Durable supervisor used as the default owner of graph/team execution."""

    _LEASE_HEARTBEAT_INTERVAL_SECONDS = 30

    def __init__(
        self, root: str | Path, *, company: AutonomousCompany | None = None,
        evidence_signer: Callable[[bytes], Mapping[str, str]] | None = None,
        provider_resume_authorizer: Callable[[str, str, str, str], bool] | None = None,
        agent_assignment_verifier: Callable[[Mapping[str, Any]], Any] | None = None,
        workflow_profile_path: str | Path | None = None,
    ) -> None:
        if provider_resume_authorizer is not None and not callable(provider_resume_authorizer):
            raise TypeError("provider_resume_authorizer must be callable")
        self._provider_resume_authorizer = provider_resume_authorizer
        if agent_assignment_verifier is not None and not callable(agent_assignment_verifier):
            raise TypeError("agent_assignment_verifier must be callable")
        # Host-owned verifier is injected by trusted bootstrap and re-used after restart.
        # It must authenticate the assignment from host-controlled identity/policy,
        # not trust the persisted authorization marker or digest alone.
        self._agent_assignment_verifier = agent_assignment_verifier
        self.root = Path(root).expanduser().resolve()
        self.company = company or AutonomousCompany(self.root)
        self.db_path = self.root / ".auren" / "execution.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Preferences are machine/project-local and are loaded fail-closed.
        # The effective profile is copied into each new task's immutable intent.
        self.workflow_profile_path = (
            Path(workflow_profile_path).expanduser()
            if workflow_profile_path is not None
            else self.db_path.parent / "workflow_profile.json"
        )
        self.progress_journal = ProgressJournal(self.db_path.parent / "progress.jsonl")
        self._progress_journal_error: str | None = None
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, goal TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handlers(
              name TEXT PRIMARY KEY, reference TEXT NOT NULL, updated_at TEXT NOT NULL,
              ci_gate_policy_json TEXT NOT NULL DEFAULT '{}',
              promotion_gate_policy_json TEXT NOT NULL DEFAULT '{}',
              approval_gate_policy_json TEXT NOT NULL DEFAULT '{}',
              execution_class TEXT NOT NULL DEFAULT 'unclassified',
              implementation_digest TEXT NOT NULL DEFAULT '',
              guard_action TEXT NOT NULL DEFAULT '',
              agent_action_class TEXT NOT NULL DEFAULT '');
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
            if "approval_gate_policy_json" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN approval_gate_policy_json TEXT NOT NULL DEFAULT '{}'")
            if "execution_class" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN execution_class TEXT NOT NULL DEFAULT 'unclassified'")
            if "implementation_digest" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN implementation_digest TEXT NOT NULL DEFAULT ''")
            if "guard_action" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN guard_action TEXT NOT NULL DEFAULT ''")
            if "agent_action_class" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN agent_action_class TEXT NOT NULL DEFAULT ''")
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
                ("work_key", "TEXT"), ("intent_digest", "TEXT"),
                ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
                ("cancel_reason_digest", "TEXT"), ("deadline_at", "TEXT"),
                ("deadline_seconds", "REAL"),
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {declaration}")
            # A stable key identifies one durable submission, not merely a shared work-unit label.
            # Legacy rows keep NULL keys; new keyed submissions are serialized by this unique index.
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS tasks_work_key_unique "
                "ON tasks(work_key) WHERE work_key IS NOT NULL AND work_key <> ''"
            )
        self.evidence_ledger = CanonicalEvidenceLedger(
            self.root / ".auren" / "canonical-evidence.sqlite3"
        )
        self.evidence_capture = EvidenceCapture.from_environment(
            self.root / ".auren" / "engineering-evidence.sqlite3",
            evidence_ledger=self.evidence_ledger,
            signer=evidence_signer,
        )
        # One supervisor-owned graph is the canonical store for revision-bound
        # execution evidence. Callers must not substitute task-payload receipts.
        self.evidence_project = __import__("os").environ.get("AUREN_REPOSITORY_SLUG", "").strip() or self.root.name
        self.evidence_memory = PersistentMemory(
            self.root / ".auren" / "durable-evidence-graph.sqlite3", require_approval=False
        )
        self.evidence_gate_integration = EvidenceGateIntegration(self.evidence_memory, self.evidence_project)
        # Registered by EngineeringPlanDispatch; keyed by the durable plan-store path.
        # A restarted worker must explicitly restore this trusted bridge before execution.
        self._engineering_plan_recovery_bridges: dict[str, Any] = {}
        # Provider adapters are restored by the host on worker startup. Missing
        # adapters never authorize a retry; ambiguous effects remain blocked.
        self._provider_operation_adapters: dict[str, Any] = {}
        self._cancellation_events: dict[str, Event] = {}
        self._cancellation_events_lock = __import__("threading").RLock()
        self._progress_reconciliation_findings: list[str] = []
        self._progress_reconciliation_incomplete = False
        self._submission_reconciliation_findings: list[str] = []
        self._submission_reconciliation_incomplete = False
        self._terminal_reconciliation_findings: list[str] = []
        self._terminal_reconciliation_incomplete = False
        self._graph_checkpoint_reconciliation_findings: list[str] = []
        self._graph_checkpoint_reconciliation_incomplete = False
        self.reconcile_progress_journal()
        self.reconcile_submission_events()
        self.reconcile_terminal_events()
        self.reconcile_approval_waits()
        self.inspect_graph_checkpoints()

    def register_handler(
        self,
        name: str,
        handler: Handler | str,
        *,
        ci_gate_policy: Mapping[str, Any] | None = None,
        promotion_gate_policy: Mapping[str, Any] | None = None,
        approval_gate_policy: Mapping[str, Any] | None = None,
        execution_class: str | None = None,
        guard_action: str | None = None,
        agent_action_class: str | None = None,
    ) -> str:
        """Register a durable handler and trusted, operator-owned gate policies.

        Execution class is an operator-owned registration contract, persisted
        separately from task payload. New handlers must explicitly be classified
        as read_only, context_provider, ci_gated, consequential, or approval_gated. Unknown legacy
        registrations fail closed until reclassified. Consequential handlers
        require both trusted decision-record and exact-head CI gates. Context
        providers are internal-only and cannot be submitted as tasks. For assigned
        work, `agent_action_class` is an operator-owned handler contract and must
        match the assignment exactly; it never replaces Guard or approval gates.
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
        if guard_action is not None:
            if (not isinstance(guard_action, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", guard_action)):
                raise ValueError("guard_action must be a lowercase trusted action label")
            inferred_delivery_action = infer_delivery_action(name, {})
            if inferred_delivery_action is not None and guard_action != inferred_delivery_action:
                raise ValueError("guard_action cannot contradict the handler's delivery action")
        if agent_action_class is not None:
            if (not isinstance(agent_action_class, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent_action_class)):
                raise ValueError("agent_action_class must be a lowercase trusted action class")
            try:
                organization_registry, _ = load_agent_org_registry()
            except Exception as exc:
                raise ValueError("agent action classes require a valid organization registry") from exc
            allowed_agent_action_classes = {
                action_class
                for role in organization_registry["roles"]
                for action_class in role["allowed_action_classes"]
            }
            if agent_action_class not in allowed_agent_action_classes:
                raise ValueError("agent_action_class is not declared by any organization role")
            delivery_guard_tokens = set(re.findall(r"[a-z0-9]+", (guard_action or "").lower()))
            if (
                infer_delivery_action(name, {}) is not None
                or infer_delivery_action(str(getattr(resolved_handler, "__qualname__", "")), {}) is not None
                or delivery_guard_tokens & {"commit", "push", "pull", "merge", "release", "deploy", "publish", "approve", "promote"}
            ):
                raise ValueError("delivery handlers cannot be assigned a non-delivery role action class")

        name_tokens = _handler_tokens(name)
        if isinstance(handler, str):
            handler_identity = handler.split(":", 1)[-1]
        else:
            handler_identity = str(getattr(handler, "__qualname__", ""))
        identity_tokens = _handler_tokens(handler_identity)
        tokens = name_tokens | identity_tokens
        explicit_action_tokens = {"promote", "promotion", "promoter", "merge", "merger", "release", "deploy", "deployment", "publish"}
        provider_action_exceptions = {"merge", "merger", "release", "deploy", "deployment", "publish", "promote", "promoter"}

        with closing(sqlite3.connect(self.db_path)) as db, db:
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
        if execution_class not in {"read_only", "context_provider", "ci_gated", "consequential", "approval_gated"}:
            raise ValueError(
                "handler registration requires an explicit execution_class: "
                "read_only, context_provider, ci_gated, consequential, or approval_gated"
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
            with closing(sqlite3.connect(self.db_path)) as db, db:
                prior = db.execute(
                    "SELECT promotion_gate_policy_json FROM handlers WHERE name=?", (name,)
                ).fetchone()
            if not prior or not json.loads(prior[0] or "{}"):
                raise ValueError(
                    f"consequential handler {name!r} requires an operator-owned promotion_gate_policy"
                )
        if requires_exact_head_ci and not ci_gate_policy:
            with closing(sqlite3.connect(self.db_path)) as db, db:
                prior = db.execute(
                    "SELECT ci_gate_policy_json FROM handlers WHERE name=?", (name,)
                ).fetchone()
            if not prior or not json.loads(prior[0] or "{}"):
                raise ValueError(
                    f"consequential handler {name!r} requires an exact-head ci_gate_policy"
                )

        if execution_class == "approval_gated" and not approval_gate_policy:
            with closing(sqlite3.connect(self.db_path)) as db, db:
                prior = db.execute(
                    "SELECT approval_gate_policy_json FROM handlers WHERE name=?", (name,)
                ).fetchone()
            if not prior or not json.loads(prior[0] or "{}"):
                raise ValueError(
                    f"approval-gated handler {name!r} requires an operator-owned approval_gate_policy"
                )

        approval_policy = dict(approval_gate_policy or {})
        if approval_policy:
            if (
                approval_policy.get("approval_store") != "DurableApprovalStore"
                or approval_policy.get("gateway") != "ActionGovernanceGateway"
                or approval_policy.get("durable_side_effect_ledger") is not True
            ):
                raise ValueError(
                    "approval_gate_policy must bind DurableApprovalStore, ActionGovernanceGateway, "
                    "and durable_side_effect_ledger=true"
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
            # Optional operator-owned argv checks let the supervisor create the
            # durable receipt itself, instead of relying on every provider to
            # implement receipt persistence correctly. These values are persisted
            # with the handler registration and never read from task payload.
            raw_evidence_checks = promotion_policy.get("evidence_checks", [])
            if not isinstance(raw_evidence_checks, (list, tuple)):
                raise ValueError("promotion evidence_checks must be a list of trusted check definitions")
            normalized_checks = []
            seen_check_names = set()
            for item in raw_evidence_checks:
                if not isinstance(item, Mapping):
                    raise ValueError("each promotion evidence check must be a mapping")
                check_name, argv = item.get("name"), item.get("argv")
                timeout = item.get("timeout_seconds", 120.0)
                if not isinstance(check_name, str) or not check_name.strip() or check_name in seen_check_names:
                    raise ValueError("promotion evidence check names must be non-empty and unique")
                if (not isinstance(argv, (list, tuple)) or not argv
                        or not all(isinstance(arg, str) and arg for arg in argv)):
                    raise ValueError("promotion evidence check argv must be a non-empty list of strings")
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                    raise ValueError("promotion evidence check timeout_seconds must be positive")
                normalized_checks.append({
                    "name": check_name,
                    "argv": list(argv),
                    "timeout_seconds": float(timeout),
                })
                seen_check_names.add(check_name)
            promotion_policy = {"context_handler": context_handler, "evidence_checks": normalized_checks}

        with closing(sqlite3.connect(self.db_path)) as db, db:
            prior = db.execute(
                "SELECT ci_gate_policy_json,promotion_gate_policy_json,guard_action,agent_action_class FROM handlers WHERE name=?",
                (name,),
            ).fetchone()
            prior_guard_action = str(prior[2] or "") if prior else ""
            prior_agent_action_class = str(prior[3] or "") if prior else ""
            if guard_action is None:
                guard_action = prior_guard_action
            elif prior_guard_action and guard_action != prior_guard_action:
                raise ValueError("registered guard_action is immutable; use a new handler identity for a different action")
            if agent_action_class is None:
                agent_action_class = prior_agent_action_class or None
            elif prior_agent_action_class and agent_action_class != prior_agent_action_class:
                raise ValueError("registered agent_action_class is immutable; use a new handler identity for a different action class")
            # Re-registering a handler after restart must not silently remove
            # either trusted gate when the caller omits or empties its policy.
            if not ci_policy and prior:
                ci_policy = json.loads(prior[0] or "{}")
            if not promotion_policy and prior:
                promotion_policy = json.loads(prior[1] or "{}")
            prior_approval_policy = db.execute(
                "SELECT approval_gate_policy_json FROM handlers WHERE name=?", (name,)
            ).fetchone()
            if not approval_policy and prior_approval_policy:
                approval_policy = json.loads(prior_approval_policy[0] or "{}")
            db.execute(
                "INSERT INTO handlers(name,reference,updated_at,ci_gate_policy_json,promotion_gate_policy_json,approval_gate_policy_json,execution_class,implementation_digest,guard_action,agent_action_class) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "reference=excluded.reference,updated_at=excluded.updated_at,"
                "ci_gate_policy_json=excluded.ci_gate_policy_json,"
                "promotion_gate_policy_json=excluded.promotion_gate_policy_json,"
                "approval_gate_policy_json=excluded.approval_gate_policy_json,"
                "execution_class=excluded.execution_class,"
                "implementation_digest=excluded.implementation_digest,"
                "guard_action=excluded.guard_action,"
                "agent_action_class=excluded.agent_action_class",
                (name, reference, _now(), json.dumps(ci_policy, sort_keys=True),
                 json.dumps(promotion_policy, sort_keys=True), json.dumps(approval_policy, sort_keys=True),
                 execution_class, implementation_digest, guard_action or "", agent_action_class or ""),
            )
        return reference

    def _handler_guard_action(self, name: str) -> str:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("SELECT guard_action FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        value = row[0]
        return value if isinstance(value, str) else ""

    def _handler_agent_action_class(self, name: str) -> str | None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("SELECT agent_action_class FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        value = row[0]
        return value if isinstance(value, str) and value else None

    def _evaluate_guard_for_task(self, task: DurableTask) -> GuardDecision:
        """Evaluate current organization policy using trusted handler registration context."""
        policy = load_guard_policy(self.root / ".auren" / "guard_policy.json")
        execution_class = self._handler_execution_class(task.handler)
        action = infer_delivery_action(task.handler, {}) or self._handler_guard_action(task.handler)
        resource = task.payload.get("resource", task.payload.get("resource_id", task.payload.get("path", "")))
        if resource is not None and not isinstance(resource, str):
            raise GuardPolicyError("Guard resource selector must be a string")
        # Assignment envelopes are untrusted durable data. Re-validate their
        # digest, current registry contract, action class, and resource scope
        # on every dispatch, including after restart. A role label alone never
        # grants permission; handlers must still enforce scope at their actual
        # filesystem/tool boundary.
        validate_agent_assignment_for_dispatch(
            task.payload, resource=resource,
            authorization_verifier=self._agent_assignment_verifier,
            expected_action_class=self._handler_agent_action_class(task.handler),
            expected_policy_version=POLICY_VERSION,
            expected_policy_digest=guard_policy_fingerprint(policy),
        )
        organization_id = __import__("os").environ.get("AUREN_ORGANIZATION_ID", "")
        approval_policy = self._handler_approval_gate_policy(task.handler)
        return evaluate_guard_policy(
            policy,
            {
                "action": action,
                "tool": task.handler,
                "resource": resource if isinstance(resource, str) else "",
                "organization_id": organization_id,
                "agent_id": task.handler,
                "risk": "high" if execution_class in {"consequential", "approval_gated"} else "standard",
            },
            mandatory_authorized=True,
            approval_gate_configured=(execution_class == "approval_gated" and bool(approval_policy)),
        )

    def _handler_execution_class(self, name: str) -> str:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("SELECT execution_class FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        value = row[0]
        return value if isinstance(value, str) else "unclassified"

    def _handler_ci_gate_policy(self, name: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("SELECT ci_gate_policy_json FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        policy = json.loads(row[0] or "{}")
        if not isinstance(policy, dict):
            raise ValueError(f"invalid persisted CI gate policy for handler: {name}")
        return policy

    def _handler_approval_gate_policy(self, name: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("SELECT approval_gate_policy_json FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        policy = json.loads(row[0] or "{}")
        if not isinstance(policy, dict):
            raise ValueError(f"invalid persisted approval gate policy for handler: {name}")
        if policy and (
            policy.get("approval_store") != "DurableApprovalStore"
            or policy.get("gateway") != "ActionGovernanceGateway"
            or policy.get("durable_side_effect_ledger") is not True
        ):
            raise ValueError(f"approval gate policy is incomplete for handler: {name}")
        return policy

    def _handler_promotion_gate_policy(self, name: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as db, db:
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
        with closing(sqlite3.connect(self.db_path)) as db, db:
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

    def run_evidence_gate(
        self,
        *,
        run_id: str,
        expected_revision: str,
        checks: tuple[EvidenceCheck, ...] | list[EvidenceCheck],
        total_timeout_seconds: float = 600.0,
        output_limit_chars: int = 4000,
    ) -> IntegratedEvidenceReceipt:
        """Run bounded checks and persist their receipt in the supervisor-owned graph."""
        return self.evidence_gate_integration.run_and_record(
            self.root,
            run_id=run_id,
            expected_revision=expected_revision,
            checks=checks,
            total_timeout_seconds=total_timeout_seconds,
            output_limit_chars=output_limit_chars,
        )

    def _require_durable_evidence_receipt(
        self, receipt_node_id: Any, expected_head: str, *, expected_run_id: str | None = None
    ) -> str:
        """Require a readable PASS receipt, bound to this exact candidate revision and optional invocation."""

        if not isinstance(receipt_node_id, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_node_id):
            raise RuntimeError("durable evidence receipt is missing or malformed")
        graph = self.evidence_gate_integration.graph
        node = graph.get_node(receipt_node_id)
        if node is None or node.kind != "evidence_gate_receipt":
            raise RuntimeError("durable evidence receipt is absent from the supervisor-owned graph")
        metadata = dict(node.metadata)
        if metadata.get("project") != self.evidence_project:
            raise RuntimeError("durable evidence receipt belongs to a different project")
        if metadata.get("status") != "PASS":
            raise RuntimeError("durable evidence receipt did not PASS")
        if metadata.get("expected_revision") != expected_head or metadata.get("observed_revision") != expected_head:
            raise RuntimeError("durable evidence receipt is stale for the live promotion candidate")
        raw = metadata.get("receipt_json")
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("durable evidence receipt payload is missing")
        try:
            receipt = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("durable evidence receipt payload is malformed") from exc
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("digest") != node.digest
            or receipt.get("status") != "PASS"
            or receipt.get("expected_revision") != expected_head
            or receipt.get("observed_revision") != expected_head
        ):
            raise RuntimeError("durable evidence receipt integrity or revision binding failed")
        checks = receipt.get("checks")
        if not isinstance(checks, list) or not checks or any(
            not isinstance(check, Mapping) or check.get("status") != "PASS" for check in checks
        ):
            raise RuntimeError("durable evidence receipt has missing or failed check results")
        incoming = graph.lineage(receipt_node_id, direction="in")
        run_provenance_ok = False
        for edge in incoming:
            if edge.relation != "records_receipt":
                continue
            source = graph.get_node(edge.source_id)
            if source is None or source.kind != "evidence_gate_run":
                continue
            source_metadata = dict(source.metadata)
            if (
                source_metadata.get("project") == self.evidence_project
                and source_metadata.get("receipt_digest") == node.digest
                and source_metadata.get("status") == "PASS"
                and isinstance(source_metadata.get("run_id"), str)
                and source_metadata.get("run_id", "").strip()
                and (expected_run_id is None or source_metadata.get("run_id") == expected_run_id)
            ):
                run_provenance_ok = True
                break
        if not run_provenance_ok:
            raise RuntimeError("durable evidence receipt has no matching persisted run provenance")
        outgoing = graph.lineage(receipt_node_id, direction="out")
        persisted_checks = []
        for edge in outgoing:
            if edge.relation != "contains_check":
                continue
            check_node = graph.get_node(edge.target_id)
            if check_node is not None and check_node.kind == "evidence_gate_check":
                persisted_checks.append(dict(check_node.metadata))
        expected_check_names = {str(check.get("name")) for check in checks if isinstance(check, Mapping)}
        actual_check_names: set[str] = set()
        for check_metadata in persisted_checks:
            if (
                check_metadata.get("project") != self.evidence_project
                or check_metadata.get("receipt_digest") != node.digest
                or check_metadata.get("status") != "PASS"
            ):
                raise RuntimeError("durable evidence graph contains a failed or mismatched check node")
            result_json = check_metadata.get("result_json")
            try:
                check_result = json.loads(result_json) if isinstance(result_json, str) else None
            except (TypeError, ValueError):
                check_result = None
            if not isinstance(check_result, Mapping) or check_result.get("status") != "PASS":
                raise RuntimeError("durable evidence check details are missing or invalid")
            actual_check_names.add(str(check_metadata.get("name", "")))
        if len(persisted_checks) != len(checks) or actual_check_names != expected_check_names:
            raise RuntimeError("durable evidence graph check lineage is incomplete")
        return receipt_node_id

    def _preflight_gates(self, task: DurableTask) -> tuple[bool, str, tuple[str, ...]]:
        """Validate operator-owned handler class and required gates before side effects."""
        evidence: list[str] = []
        promotion_expected_head: str | None = None
        try:
            execution_class = self._handler_execution_class(task.handler)
            if execution_class == "context_provider":
                return False, "internal context providers cannot be executed as task handlers", ("handler-class:rejected",)
            if execution_class not in {"read_only", "ci_gated", "consequential", "approval_gated"}:
                return False, "unclassified legacy handler fails closed", ("handler-class:unclassified",)
            profile = WorkflowProfile.from_mapping(task.payload["_auren_workflow_profile"])
            delivery_action = infer_delivery_action(task.handler, task.payload)
            # Organization policy adds restrictions before dispatch. It is not an
            # authority grant: existing approval, exact-head CI, and promotion
            # gates below remain independently mandatory.
            try:
                guard_decision = self._evaluate_guard_for_task(task)
            except (GuardPolicyError, OSError, ValueError) as exc:
                return False, f"configured Guard policy could not be verified: {type(exc).__name__}", ("guard-policy:invalid", "guard-policy:blocked")
            if not guard_decision.allowed:
                return False, guard_decision.reason, (
                    "guard-policy:" + guard_decision.policy_digest,
                    "guard-decision:" + guard_decision.effect,
                    *("guard-rule:" + rule_id for rule_id in guard_decision.matched_rule_ids),
                )
            evidence.extend((
                "guard-policy:" + guard_decision.policy_digest,
                "guard-decision:" + guard_decision.effect,
            ))
            # Stage-only is a hard delivery boundary, not just a UI preference.
            # Block every consequential/approval-gated operation even if the
            # handler uses a generic name or omits an action field.
            if profile.delivery_mode == "stage_only" and execution_class in {"consequential", "approval_gated"}:
                return False, (
                    "stage-only workflow profile forbids consequential or approval-gated actions; "
                    "leave repository changes staged and do not commit, push, create a PR, or merge"
                ), ("workflow-profile:stage-only", "delivery-action:blocked")
            if delivery_action is not None and not delivery_mode_allows(profile, delivery_action):
                return False, (
                    f"workflow profile delivery mode {profile.delivery_mode!r} forbids {delivery_action!r}; "
                    "no repository delivery side effect was dispatched"
                ), (f"workflow-profile:{profile.delivery_mode}", f"delivery-action:{delivery_action}:blocked")
            if delivery_action is not None and execution_class == "read_only":
                return False, (
                    f"repository delivery action {delivery_action!r} cannot run through a read-only handler"
                ), ("delivery-action:blocked", "handler-class:read-only")
            promotion_actions = {"merge", "publish", "release", "deploy"}
            if delivery_action in promotion_actions and execution_class not in {"consequential", "approval_gated"}:
                return False, (
                    f"promotion action {delivery_action!r} requires a consequential or approval-gated handler"
                ), ("delivery-action:promotion-gate-required", "handler-class:rejected")
            configured_promotion_policy = self._handler_promotion_gate_policy(task.handler)
            configured_ci_policy = self._handler_ci_gate_policy(task.handler)
            configured_approval_policy = self._handler_approval_gate_policy(task.handler)
            if delivery_action in promotion_actions and (not configured_promotion_policy or not configured_ci_policy):
                return False, (
                    f"promotion action {delivery_action!r} requires operator-owned promotion policy and exact-head CI policy"
                ), ("promotion-gate:missing", "ci-gate:missing")
            if execution_class == "approval_gated" and not configured_approval_policy:
                return False, "approval-gated handler is missing its trusted gateway/ledger policy", ("approval-gate:missing",)
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
                if execution_class == "consequential":
                    configured_checks = promotion_policy.get("evidence_checks", [])
                    expected_evidence_run_id = None
                    if configured_checks:
                        # Bind the receipt invocation to this durable task, attempt,
                        # and operation payload. This prevents a PASS receipt from
                        # one promotion request being replayed for a different task.
                        operation_digest = hashlib.sha256(
                            json.dumps(task.payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                        ).hexdigest()
                        expected_evidence_run_id = (
                            f"promotion:{task.id}:{task.attempts}:{operation_digest}"
                        )
                        integrated = self.run_evidence_gate(
                            run_id=expected_evidence_run_id,
                            expected_revision=expected_head,
                            checks=tuple(
                                EvidenceCheck(
                                    item["name"], tuple(item["argv"]), item["timeout_seconds"]
                                )
                                for item in configured_checks
                            ),
                        )
                        supplied_receipt_id = integrated.receipt_node_id
                    else:
                        # Backwards-compatible path for trusted providers that
                        # already run and persist the complete evidence gate.
                        supplied_receipt_id = context.get("evidence_receipt_node_id")
                    receipt_node_id = self._require_durable_evidence_receipt(
                        supplied_receipt_id, expected_head, expected_run_id=expected_evidence_run_id
                    )
                    evidence.extend((f"durable-evidence-receipt:{receipt_node_id}", "durable-evidence-receipt:passed"))
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

    def reconcile_progress_journal(
        self, *, limit: int = 1000, time_budget_seconds: float = 2.0
    ) -> dict[str, Any]:
        """Rebuild the progress projection from authoritative task and receipt stores.

        The SQLite task row alone cannot prove completion: the executive work-unit
        receipt must agree. A mismatch is projected as blocked, never success. The
        scan is bounded so a damaged or very large journal cannot hold startup open.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("reconciliation limit must be an integer from 1 to 10000")
        if (isinstance(time_budget_seconds, bool)
                or not isinstance(time_budget_seconds, (int, float))
                or not math.isfinite(time_budget_seconds)
                or not 0.05 <= time_budget_seconds <= 30.0):
            raise ValueError("reconciliation time budget must be between 0.05 and 30 seconds")
        started = time.monotonic()
        deadline = started + float(time_budget_seconds)
        findings: list[str] = []
        reconciled = 0
        scanned = 0
        incomplete = False
        try:
            events = self.progress_journal._events()
            latest_by_task: dict[str, dict[str, Any]] = {}
            for event in events:
                latest_by_task[str(event["task_id"])] = event
        except Exception as exc:
            self._progress_journal_error = type(exc).__name__
            self._progress_reconciliation_findings = ["journal-unreadable"]
            self._progress_reconciliation_incomplete = True
            return {"scanned": 0, "reconciled": 0, "blocked": 1, "incomplete": True, "next_action": "repair or restore the journal, then rerun reconciliation"}

        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            total = int(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            rows = db.execute(
                "SELECT id,state,work_unit_id,heartbeat_at,lease_until,updated_at "
                "FROM tasks ORDER BY updated_at DESC,id LIMIT ?",
                (limit,),
            ).fetchall()

        now = datetime.now(timezone.utc)
        for task_id, task_state, work_unit_id, heartbeat_at, lease_until, updated_at in rows:
            if time.monotonic() >= deadline:
                incomplete = True
                break
            scanned += 1
            reason = ""
            try:
                work_state, _iteration = self.company._state(str(work_unit_id))
            except Exception:
                work_state = "unknown"

            if task_state in {"completed", "failed", "cancelled"}:
                if work_state == task_state:
                    target_state = str(task_state)
                    reason = (
                        f"SQLite task state and executive work-unit receipt agree on terminal state {task_state}."
                    )
                else:
                    # Keep the canonical task row fail-closed as well as the
                    # progress projection. A terminal SQLite row is not proof
                    # of completion when the durable work-unit receipt disagrees.
                    with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
                        cursor = db.execute(
                            "UPDATE tasks SET state='blocked',updated_at=?,heartbeat_at=NULL,"
                            "lease_until=NULL,lease_token=NULL WHERE id=? AND state=?",
                            (_now(), str(task_id), str(task_state)),
                        )
                    target_state = "blocked"
                    reason = (
                        "Task and executive receipt disagree at startup; terminal success is not trusted. "
                        "The task was blocked in the canonical store. Inspect the durable work-unit receipt "
                        "and reconcile before retry."
                    )
                    findings.append(str(task_id))
                    if cursor.rowcount != 1:
                        reason += " The task row changed concurrently; refresh canonical state before acting."
            elif work_state in {"completed", "failed", "cancelled"}:
                target_state = "blocked"
                reason = (
                    "Executive work unit is terminal while the task row is non-terminal; "
                    "reconcile the stores before retry or claiming completion."
                )
                findings.append(str(task_id))
            elif task_state == "pending" and work_state == "running":
                target_state = "queued"
                reason = "Startup reconciliation restored queued status from the durable task row."
            elif task_state == "blocked_quota" and work_state == "running":
                target_state = "blocked"
                reason = "Task remains blocked on provider quota; resume only after its durable retry time."
            elif task_state in {"waiting_approval", "blocked_approval"} and work_state in {"waiting_external", "running"}:
                target_state = "blocked"
                reason = "Task is waiting for an explicit human approval decision; do not redispatch before the saved checkpoint is approved."
            elif task_state == "running" and work_state == "running":
                lease_live = False
                try:
                    lease_dt = datetime.fromisoformat(str(lease_until)) if lease_until else None
                    heartbeat_dt = datetime.fromisoformat(str(heartbeat_at)) if heartbeat_at else None
                    if lease_dt is not None and heartbeat_dt is not None:
                        if lease_dt.tzinfo is not None and heartbeat_dt.tzinfo is not None:
                            lease_live = (
                                lease_dt > now
                                and (now - heartbeat_dt).total_seconds() <= 90
                            )
                except (TypeError, ValueError):
                    lease_live = False
                if lease_live:
                    target_state = "running"
                    reason = "SQLite lease and heartbeat are fresh; task remains owned by its active worker."
                else:
                    target_state = "recovering"
                    reason = (
                        "SQLite reports a running task but its lease or heartbeat is stale or missing; "
                        "worker liveness is unproven. Reconcile before retry."
                    )
                    findings.append(str(task_id))
            else:
                target_state = "blocked"
                reason = (
                    f"Unrecognized or inconsistent durable task state ({task_state}/{work_state}); "
                    "inspect SQLite and the executive receipt before resuming."
                )
                findings.append(str(task_id))

            previous = latest_by_task.get(str(task_id), {})
            previous_state = previous.get("state")
            previous_event = previous.get("event")
            heartbeat_newer = False
            if target_state == "running" and heartbeat_at and previous.get("timestamp"):
                try:
                    heartbeat_dt = datetime.fromisoformat(str(heartbeat_at))
                    progress_dt = datetime.fromisoformat(str(previous["timestamp"]).replace("Z", "+00:00"))
                    heartbeat_newer = (
                        heartbeat_dt.tzinfo is not None and progress_dt.tzinfo is not None
                        and (heartbeat_dt - progress_dt).total_seconds() > 30
                    )
                except (TypeError, ValueError):
                    heartbeat_newer = False
            needs_event = (
                previous_state != target_state
                or previous_event is None
                or (target_state in {"blocked", "recovering"} and previous_event != "startup_reconciliation")
                or heartbeat_newer
            )
            if not needs_event:
                continue
            try:
                event = self.progress_journal.record_reconciliation(
                    str(task_id), state=target_state, stage="startup-reconciliation",
                    message=reason, evidence_ref=f"sqlite-task:{task_id};executive-work-unit:{work_unit_id}",
                    expected_sequence=int(previous.get("sequence", 0)) if previous else 0,
                )
                latest_by_task[str(task_id)] = {
                    "task_id": event.task_id, "event": event.event, "state": event.state,
                    "timestamp": event.timestamp, "sequence": event.sequence,
                }
                reconciled += 1
            except Exception as exc:
                self._progress_journal_error = type(exc).__name__
                findings.append(str(task_id))

        if total > len(rows) or scanned < len(rows):
            incomplete = True
        self._progress_reconciliation_findings = list(dict.fromkeys(findings))
        self._progress_reconciliation_incomplete = incomplete
        return {
            "scanned": scanned,
            "reconciled": reconciled,
            "blocked": len(self._progress_reconciliation_findings),
            "incomplete": incomplete,
            "next_action": (
                "rerun reconcile_progress_journal with a larger limit or time budget; "
                "do not trust unscanned task projections"
            ) if incomplete else None,
        }

    def _record_progress(
        self, task_id: str, event: str, *, state: str, stage: str, message: str,
        evidence_ref: str | None = None,
    ) -> None:
        """Project supervisor transitions into the truthful progress journal.

        Task state remains authoritative in SQLite. Journal failures are surfaced
        through health_findings instead of falsely changing the task outcome.
        """
        try:
            self.progress_journal.record(
                task_id, event, state=state, stage=stage, message=message,
                evidence_ref=evidence_ref,
            )
        except Exception as exc:
            if self._progress_journal_error is None:
                self._progress_journal_error = type(exc).__name__

    def _capture_event(
        self, event_type: str, task: DurableTask, *, status: str,
        metadata: Mapping[str, Any] | None = None,
        event_id: str | None = None,
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
        safe_metadata = dict(metadata or {})
        # Bind lifecycle evidence to the immutable effective workflow profile.
        # Do not copy task business payload into the receipt.
        profile_data = task.payload.get("_auren_workflow_profile")
        profile_digest = task.payload.get("_auren_workflow_profile_digest")
        if isinstance(profile_data, Mapping) and isinstance(profile_digest, str):
            try:
                effective_profile = WorkflowProfile.from_mapping(profile_data)
                if effective_profile.digest != profile_digest:
                    raise RuntimeError("workflow profile receipt digest mismatch")
                safe_metadata["workflow_profile_version"] = effective_profile.profile_version
                safe_metadata["workflow_profile_digest"] = effective_profile.digest
                safe_metadata["workflow_delivery_mode"] = effective_profile.delivery_mode
                safe_metadata["workflow_execution_mode"] = effective_profile.execution_mode
                safe_metadata["workflow_approval_mode"] = effective_profile.approval_mode
                safe_metadata["workflow_review_mode"] = effective_profile.review_mode
                safe_metadata["workflow_recovery_mode"] = effective_profile.recovery_mode
            except (TypeError, ValueError) as exc:
                raise RuntimeError("cannot record invalid workflow profile receipt") from exc
        try:
            configured_policy = load_guard_policy(self.root / ".auren" / "guard_policy.json")
            if "guard_policy_digest" not in safe_metadata:
                safe_metadata["guard_policy_digest"] = evaluate_guard_policy(
                    configured_policy, {},
                ).policy_digest
        except (GuardPolicyError, OSError, ValueError):
            safe_metadata["guard_policy_status"] = "invalid"
        self.evidence_capture.record(
            event_type,
            work_unit_id=task.work_unit_id,
            task_id=task.id,
            iteration=task.attempts,
            status=status,
            repository=repository,
            head_sha=head_sha,
            metadata=safe_metadata,
            event_id=event_id,
        )

    def _capture_terminal_event(
        self, event_type: str, task: DurableTask, *, status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Project terminal evidence only after task and executive receipts commit."""
        try:
            self._capture_event(
                event_type,
                task,
                status=status,
                metadata=metadata,
                event_id=f"task-terminal:{task.id}:{status}",
            )
        except Exception:
            # SQLite task and work-unit receipt are authoritative. Keep the audit
            # gap visible and let bounded startup reconciliation repair the event.
            self._terminal_reconciliation_findings = list(dict.fromkeys(
                [*self._terminal_reconciliation_findings, task.id]
            ))

    def _terminal_event_status(self, task: DurableTask) -> tuple[str, str]:
        """Validate terminal evidence against the durable task identity and outcome."""
        expected_type = "task_completed" if task.state == "completed" else "task_failed"
        with closing(sqlite3.connect(self.evidence_capture.path, timeout=5)) as db, db:
            rows = db.execute(
                "SELECT event_type,work_unit_id,task_id,iteration,status,metadata_json "
                "FROM engineering_events WHERE task_id=? "
                "AND event_type IN ('task_completed','task_failed') ORDER BY sequence",
                (task.id,),
            ).fetchall()
        if not rows:
            return "missing", ""
        if len(rows) != 1:
            return "blocked", "multiple terminal evidence records exist for one task"
        event_type, work_unit_id, task_id, iteration, status, metadata_json = rows[0]
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, json.JSONDecodeError):
            return "blocked", "terminal evidence metadata is corrupt"
        expected_category = "success" if task.state == "completed" else "failure"
        if (
            event_type != expected_type
            or work_unit_id != task.work_unit_id
            or task_id != task.id
            or isinstance(iteration, bool)
            or not isinstance(iteration, int)
            or iteration != task.attempts
            or status != task.state
            or not isinstance(metadata, Mapping)
            or metadata.get("handler") != task.handler
            or metadata.get("result_category") not in {None, expected_category}
        ):
            return "blocked", "terminal evidence does not match the durable task identity and outcome"
        return "present", ""

    def reconcile_terminal_events(
        self, *, limit: int = 1000, time_budget_seconds: float = 2.0
    ) -> dict[str, Any]:
        """Repair missing terminal evidence only when task and executive receipts agree.

        A task-row terminal state alone is insufficient. Conflicting task/work-unit
        state or duplicate/mismatched evidence remains blocked. This method never
        invokes a handler or replays an external side effect.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("terminal reconciliation limit must be an integer from 1 to 10000")
        if (
            isinstance(time_budget_seconds, bool)
            or not isinstance(time_budget_seconds, (int, float))
            or not math.isfinite(time_budget_seconds)
            or not 0.05 <= time_budget_seconds <= 30.0
        ):
            raise ValueError("terminal reconciliation time budget must be between 0.05 and 30 seconds")
        deadline = time.monotonic() + float(time_budget_seconds)
        findings: list[str] = []
        repaired = 0
        scanned = 0
        incomplete = False
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            total = int(db.execute(
                "SELECT COUNT(*) FROM tasks WHERE state IN ('completed','failed')"
            ).fetchone()[0])
            rows = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks "
                "WHERE state IN ('completed','failed') ORDER BY updated_at DESC,id LIMIT ?",
                (limit,),
            ).fetchall()
        for task_id, handler, payload_json, task_state, attempts, work_unit_id in rows:
            if time.monotonic() >= deadline:
                incomplete = True
                break
            scanned += 1
            try:
                payload = json.loads(payload_json)
                if not isinstance(payload, Mapping):
                    raise ValueError("task payload is not a mapping")
                task = DurableTask(
                    str(task_id), str(handler), dict(payload), str(task_state),
                    int(attempts), str(work_unit_id),
                )
                work_state, _iteration = self.company._state(task.work_unit_id)
                if work_state != task.state:
                    findings.append(task.id)
                    continue
                status, _reason = self._terminal_event_status(task)
                if status == "present":
                    continue
                if status == "blocked":
                    findings.append(task.id)
                    continue
                event_type = "task_completed" if task.state == "completed" else "task_failed"
                category = "success" if task.state == "completed" else "failure"
                self._capture_terminal_event(
                    event_type,
                    task,
                    status=task.state,
                    metadata={
                        "handler": task.handler,
                        "phase": "startup_reconciliation",
                        "result_category": category,
                    },
                )
                status, _reason = self._terminal_event_status(task)
                if status == "present":
                    repaired += 1
                else:
                    findings.append(task.id)
            except Exception:
                findings.append(str(task_id))
        if total > len(rows) or scanned < len(rows):
            incomplete = True
        self._terminal_reconciliation_findings = list(dict.fromkeys(findings))
        self._terminal_reconciliation_incomplete = incomplete
        return {
            "scanned": scanned,
            "repaired": repaired,
            "blocked": len(self._terminal_reconciliation_findings),
            "incomplete": incomplete,
            "next_action": (
                "rerun reconcile_terminal_events with a larger limit or time budget; "
                "do not trust unscanned terminal evidence"
            ) if incomplete else None,
        }

    def _submission_event_status(self, task: DurableTask) -> tuple[str, str]:
        """Validate the immutable submission event for one durable task."""
        with closing(sqlite3.connect(self.evidence_capture.path, timeout=5)) as db, db:
            rows = db.execute(
                "SELECT event_type,work_unit_id,task_id,iteration,status,metadata_json "
                "FROM engineering_events WHERE task_id=? AND event_type='task_submitted' ORDER BY sequence",
                (task.id,),
            ).fetchall()
        if not rows:
            return "missing", ""
        if len(rows) != 1:
            return "blocked", "multiple task_submitted evidence records exist for one task"
        event_type, work_unit_id, task_id, iteration, status, metadata_json = rows[0]
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, json.JSONDecodeError):
            return "blocked", "submission evidence metadata is corrupt"
        if (
            event_type != "task_submitted"
            or work_unit_id != task.work_unit_id
            or task_id != task.id
            or isinstance(iteration, bool)
            or not isinstance(iteration, int)
            or iteration != 0
            or status != "pending"
            or not isinstance(metadata, Mapping)
            or metadata.get("handler") != task.handler
            or metadata.get("phase") not in {None, "task_submission"}
        ):
            return "blocked", "submission evidence does not match the immutable task identity"
        return "present", ""

    def _ensure_submission_event(self, task: DurableTask) -> bool:
        """Create or verify one stable submission event without duplicating history."""
        status, _reason = self._submission_event_status(task)
        if status == "present":
            return True
        if status == "blocked":
            self._submission_reconciliation_findings = list(dict.fromkeys(
                [*self._submission_reconciliation_findings, task.id]
            ))
            return False
        # Submission evidence always describes the initial queued transition,
        # even if this task is being recovered after later attempts.
        initial = DurableTask(task.id, task.handler, task.payload, "pending", 0, task.work_unit_id)
        try:
            self._capture_event(
                "task_submitted",
                initial,
                status="pending",
                metadata={"handler": task.handler, "phase": "task_submission"},
                event_id=f"task-submitted:{task.id}",
            )
        except Exception:
            # The evidence transaction may have committed before an export or
            # transport failure. Re-read the canonical event store before retrying.
            pass
        status, _reason = self._submission_event_status(task)
        if status == "present":
            self._submission_reconciliation_findings = [
                item for item in self._submission_reconciliation_findings if item != task.id
            ]
            return True
        self._submission_reconciliation_findings = list(dict.fromkeys(
            [*self._submission_reconciliation_findings, task.id]
        ))
        return False

    def reconcile_submission_events(
        self, *, limit: int = 1000, time_budget_seconds: float = 2.0
    ) -> dict[str, Any]:
        """Repair missing task-submission evidence from the canonical task rows.

        This is a bounded, idempotent projection repair. It never runs handlers
        or replays side effects. Multiple or mismatched evidence records remain
        blocked for inspection instead of being overwritten.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("submission reconciliation limit must be an integer from 1 to 10000")
        if (
            isinstance(time_budget_seconds, bool)
            or not isinstance(time_budget_seconds, (int, float))
            or not math.isfinite(time_budget_seconds)
            or not 0.05 <= time_budget_seconds <= 30.0
        ):
            raise ValueError("submission reconciliation time budget must be between 0.05 and 30 seconds")
        deadline = time.monotonic() + float(time_budget_seconds)
        findings: list[str] = []
        repaired = 0
        scanned = 0
        incomplete = False
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            total = int(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            rows = db.execute(
                "SELECT id,handler,payload,work_unit_id FROM tasks ORDER BY created_at,id LIMIT ?",
                (limit,),
            ).fetchall()
        for task_id, handler, payload_json, work_unit_id in rows:
            if time.monotonic() >= deadline:
                incomplete = True
                break
            scanned += 1
            try:
                payload = json.loads(payload_json)
                if not isinstance(payload, Mapping):
                    raise ValueError("task payload is not a mapping")
                task = DurableTask(str(task_id), str(handler), dict(payload), "pending", 0, str(work_unit_id))
                status, _reason = self._submission_event_status(task)
                if status == "present":
                    continue
                if status == "blocked":
                    findings.append(str(task_id))
                    continue
                try:
                    self._capture_event(
                        "task_submitted",
                        task,
                        status="pending",
                        metadata={"handler": task.handler, "phase": "task_submission"},
                        event_id=f"task-submitted:{task.id}",
                    )
                except Exception:
                    pass
                status, _reason = self._submission_event_status(task)
                if status == "present":
                    repaired += 1
                else:
                    findings.append(str(task_id))
            except Exception:
                findings.append(str(task_id))
        if total > len(rows) or scanned < len(rows):
            incomplete = True
        self._submission_reconciliation_findings = list(dict.fromkeys(findings))
        self._submission_reconciliation_incomplete = incomplete
        return {
            "scanned": scanned,
            "repaired": repaired,
            "blocked": len(self._submission_reconciliation_findings),
            "incomplete": incomplete,
            "next_action": (
                "rerun reconcile_submission_events with a larger limit or time budget; "
                "do not assume unscanned submission evidence exists"
            ) if incomplete else None,
        }

    def work_unit_for_session(
        self, session_id: str, goal: str, *, work_key: str | None = None
    ) -> str:
        """Return a stable work unit for a session, creating it once if needed.

        Stable work keys make a retry safe if process failure occurs between
        creating the unit and committing the session-to-unit mapping. A terminal
        unit starts a new generation keyed by the previous unit ID.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal is required")
        session_id = session_id.strip()
        goal = goal.strip()
        goal_digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute(
                "SELECT work_unit_id, goal FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row:
                prior_work_unit_id = str(row[0])
                prior_state, _ = self.company._state(prior_work_unit_id)
                if prior_state in {"running", "waiting_ci", "waiting_external"}:
                    if row[1] != goal:
                        raise ValueError("session_id is already bound to a different active goal")
                    return prior_work_unit_id
                # A terminal unit for the same goal stays terminal. Starting it
                # again implicitly would turn repeated failure into an unbounded
                # retry loop; callers must provide a new session or a new goal.
                if row[1] == goal:
                    return prior_work_unit_id
                generation_key = f"execution-session:{session_id}:{goal_digest}:after:{prior_work_unit_id}"
                work = self.company.start(goal, work_key=generation_key)
                db.execute(
                    "UPDATE sessions SET work_unit_id=?,goal=?,updated_at=? WHERE session_id=?",
                    (work.id, goal, _now(), session_id),
                )
                return work.id
            stable_key = work_key.strip() if isinstance(work_key, str) and work_key.strip() else f"execution-session:{session_id}:{goal_digest}:initial"
            work = self.company.start(goal, work_key=stable_key)
            db.execute(
                "INSERT INTO sessions(session_id,work_unit_id,goal,updated_at) VALUES(?,?,?,?)",
                (session_id, work.id, goal, _now()),
            )
        return work.id
    def dispatch_engineering_plan_task(
        self,
        *,
        store: Any,
        run_id: str,
        task_id: str,
        dispatch_key: str,
        handler: str,
        packet: Any,
        payload: Mapping[str, Any] | None = None,
        goal: str | None = None,
        recovery_bridge: Any | None = None,
        deadline_seconds: float | None = None,
        shared_budget_binding: Mapping[str, Any] | None = None,
        approval_evidence: Mapping[str, Any] | None = None,
    ) -> DurableTask:
        """Dispatch one plan task through the same durable queue this controller owns.

        The adapter binds the persisted plan/run revision and fresh-context packet
        digest before queue insertion. It does not create a second execution queue.
        """
        from .engineering_plan_dispatch import EngineeringPlanDispatch

        if recovery_bridge is None:
            raise ValueError("canonical recovery bridge is required for engineering-plan dispatch")
        return EngineeringPlanDispatch(
            store=store, controller=self, recovery_bridge=recovery_bridge,
        ).dispatch(
            run_id=run_id,
            task_id=task_id,
            dispatch_key=dispatch_key,
            handler=handler,
            packet=packet,
            payload=payload,
            goal=goal,
            deadline_seconds=deadline_seconds,
            shared_budget_binding=shared_budget_binding,
            approval_evidence=approval_evidence,
        )

    def register_provider_operation_adapter(self, handler_name: str, adapter: Any) -> None:
        """Register trusted lookup and evidence-verification capabilities for a handler.

        The registered handler still performs dispatch and receives the stable
        operation ID/idempotency key in its payload. This adapter supplies the
        read-only outcome lookup and host-trusted receipt verifier.
        """
        if not isinstance(handler_name, str) or not handler_name.strip():
            raise ValueError("handler_name is required")
        if not callable(getattr(adapter, "lookup_operation", None)):
            raise TypeError("provider adapter must implement lookup_operation(operation_id, idempotency_key)")
        if not callable(getattr(adapter, "verify_reconciliation", None)):
            raise TypeError("provider adapter must implement trusted verify_reconciliation(key, evidence_ref, receipt)")
        self._provider_operation_adapters[handler_name.strip()] = adapter

    def resume_blocked_provider_task(
        self, task_id: str, *, clarification_id: str, operator_id: str, operator_reason: str,
    ) -> bool:
        """Explicitly resume a blocked task for provider reconciliation only.

        A trusted provider adapter must already be registered. The resumed
        invocation performs status lookup first; it never dispatches while the
        durable side-effect record remains ambiguous.
        """
        for value, name in (
            (task_id, "task_id"), (clarification_id, "clarification_id"),
            (operator_id, "operator_id"), (operator_reason, "operator_reason"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            row = db.execute(
                "SELECT handler,work_unit_id,state FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        handler_name, work_unit_id, task_state = str(row[0]), str(row[1]), str(row[2])
        if task_state != "blocked_reconciliation":
            raise RuntimeError("only provider-reconciliation-blocked tasks may be resumed")
        if handler_name not in self._provider_operation_adapters:
            raise RuntimeError("register a trusted provider status adapter before resuming reconciliation")
        with closing(sqlite3.connect(self.company.db_path, timeout=10)) as db, db:
            clarification = db.execute(
                "SELECT state FROM clarifications WHERE id=? AND work_unit_id=?",
                (clarification_id, work_unit_id),
            ).fetchone()
        if clarification is None:
            raise KeyError("clarification does not belong to the blocked task")
        if self._provider_resume_authorizer is None:
            raise PermissionError("trusted provider reconciliation resume authorizer is required")
        try:
            authorized = self._provider_resume_authorizer(
                task_id.strip(), clarification_id.strip(), operator_id.strip(), operator_reason.strip()
            )
        except Exception as exc:
            raise PermissionError("provider reconciliation resume authorization failed closed") from exc
        if authorized is not True:
            raise PermissionError(
                "provider reconciliation resume authorizer rejected the request; it must return literal True"
            )
        if clarification[0] == "open":
            self.company.resolve_clarification(
                clarification_id,
                f"Operator {operator_id.strip()} authorized reconciliation-only resume: {operator_reason.strip()}",
            )
        elif clarification[0] != "resolved":
            raise RuntimeError("provider clarification is not in a resolvable state")
        if self.company.open_clarifications(work_unit_id):
            raise RuntimeError("other open clarifications must be resolved before resuming the task")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            cursor = db.execute(
                "UPDATE tasks SET state='pending',updated_at=?,heartbeat_at=NULL,lease_until=NULL,"
                "lease_token=NULL WHERE id=? AND state='blocked_reconciliation'",
                (_now(), task_id),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("blocked task state changed; refresh before retrying")
        self.company.record(
            work_unit_id, "provider_operation", "operator_reconciliation_resumed",
            operator_reason.strip(), (f"operator:{operator_id.strip()}", f"task:{task_id}"),
        )
        self._record_progress(
            task_id, "provider_reconciliation_resumed", state="queued", stage="provider_operation",
            message=f"Operator {operator_id.strip()} authorized status lookup only; no remote redispatch is permitted.",
        )
        return True

    def reconcile_approval_waits(self, *, limit: int = 1000) -> int:
        """Recover approval pauses after process death without rerunning completed stages."""
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("approval recovery limit must be between 1 and 10000")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_approvals'"
            ).fetchone()
            if not exists:
                return 0
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT t.id,t.handler,t.payload,t.state,t.attempts,t.work_unit_id,t.lease_until,"
                "t.heartbeat_at,t.lease_generation,t.lease_token,a.approval_id,a.status,"
                "a.checkpoint_json,a.checkpoint_digest,a.artifact_digest,a.evidence_digest,a.policy_digest "
                "FROM tasks t JOIN durable_approvals a ON a.task_id=t.id "
                "WHERE t.state IN ('running','recovering','waiting_approval') "
                "AND a.status IN ('pending','approved','rejected','expired','cancelled','superseded',"
                "'executing','outcome_unknown','completed','failed') "
                "ORDER BY t.updated_at LIMIT ?",
                (limit,),
            ).fetchall()
            now = datetime.now(timezone.utc)
            recovered = []
            for row in rows:
                task_id, handler, raw_payload, task_state, attempts, work_unit_id = row[:6]
                lease_until, heartbeat_at, generation, lease_token = row[6:10]
                approval_id, approval_status, checkpoint_json, checkpoint_digest = row[10:14]
                artifact_digest, evidence_digest, policy_digest = row[14:17]
                lease_live = False
                try:
                    lease_dt = datetime.fromisoformat(str(lease_until)) if lease_until else None
                    heartbeat_dt = datetime.fromisoformat(str(heartbeat_at)) if heartbeat_at else None
                    if lease_dt and heartbeat_dt and lease_dt.tzinfo and heartbeat_dt.tzinfo:
                        lease_live = lease_dt > now and (now - heartbeat_dt).total_seconds() <= 90
                except (TypeError, ValueError):
                    lease_live = False
                if lease_live:
                    continue
                if task_state == "waiting_approval" and approval_status == "pending":
                    continue
                checkpoint = json.loads(checkpoint_json)
                checkpoint_digest_expected = hashlib.sha256(
                    json.dumps(checkpoint, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                ).hexdigest()
                if not isinstance(checkpoint, Mapping) or checkpoint_digest_expected != checkpoint_digest:
                    db.rollback()
                    raise RuntimeError("approval checkpoint is corrupt; task remains blocked for operator recovery")
                for key, expected in (
                    ("artifact_digest", artifact_digest), ("evidence_digest", evidence_digest),
                    ("policy_digest", policy_digest),
                ):
                    if key in checkpoint and checkpoint[key] != expected:
                        db.rollback()
                        raise RuntimeError("approval checkpoint does not match the immutable action binding")
                payload = json.loads(raw_payload)
                prior = payload.get("auren_approval_resume")
                if isinstance(prior, Mapping) and prior.get("approval_id") not in {None, approval_id}:
                    db.rollback()
                    raise RuntimeError("task checkpoint references a different approval; refusing automatic recovery")
                if approval_status == "pending":
                    next_state = "waiting_approval"
                elif approval_status == "approved":
                    next_state = "pending"
                else:
                    next_state = "blocked_approval"
                payload["auren_approval_resume"] = {
                    "approval_id": approval_id, "status": approval_status,
                    "checkpoint": checkpoint, "checkpoint_digest": checkpoint_digest,
                }
                cursor = db.execute(
                    "UPDATE tasks SET state=?,payload=?,updated_at=?,heartbeat_at=NULL,lease_until=NULL,"
                    "lease_token=NULL WHERE id=? AND state=? AND lease_generation=?",
                    (next_state, json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
                     _now(), task_id, task_state, int(generation or 0)),
                )
                if cursor.rowcount == 1:
                    recovered.append((str(task_id), str(handler), next_state, int(attempts), str(work_unit_id), str(approval_id)))
            db.commit()
        for task_id, handler, next_state, attempts, work_unit_id, approval_id in recovered:
            detail = f"Recovered approval {approval_id} after worker interruption; state={next_state}."
            self.company.record(
                work_unit_id, "approval", next_state, detail,
                (f"task:{task_id}", f"approval:{approval_id}"),
            )
            if next_state in {"waiting_approval", "blocked_approval"}:
                current_work_state, _ = self.company._state(work_unit_id)
                if current_work_state != "waiting_external":
                    self.company._transition(work_unit_id, "waiting_external")
            task = DurableTask(task_id, handler, {}, next_state, attempts, work_unit_id)
            self._capture_event("approval_recovered_after_restart", task, status=next_state, metadata={
                "approval_id": approval_id,
            })
            self._record_progress(
                task_id, "approval_recovered_after_restart",
                state="queued" if next_state == "pending" else "blocked",
                stage="approval", message=detail,
            )
        from .durable_approval import DurableApprovalStore
        DurableApprovalStore(self).recover_interrupted(older_than_seconds=300)
        return len(recovered)

    def _verify_approval_gated_result(self, task: DurableTask, result: Any) -> None:
        """Require completed durable approval receipts before an approval-gated task can finish."""
        if not isinstance(result, Mapping):
            raise RuntimeError("approval-gated handler must return a mapping with approval_receipts")
        receipt_ids = result.get("approval_receipts")
        if (
            not isinstance(receipt_ids, (list, tuple)) or not receipt_ids
            or len(receipt_ids) > 10
            or not all(isinstance(value, str) and value.strip() for value in receipt_ids)
            or len(set(receipt_ids)) != len(receipt_ids)
        ):
            raise RuntimeError("approval-gated handler must return unique completed approval receipt IDs")
        from .durable_approval import DurableApprovalStore
        store = DurableApprovalStore(self)
        if not store.verify_audit_chain():
            raise RuntimeError("approval audit chain is corrupt; task completion is blocked")
        for approval_id in receipt_ids:
            record = store.get(approval_id)
            if (
                record.task_id != task.id or record.status != "completed"
                or not record.receipt_digest
            ):
                raise RuntimeError("approval-gated task cannot complete without its own durable completed receipt")

    def _handler_payload(self, task: DurableTask, base_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Inject supervisor identity only for explicitly approval-aware handlers."""
        payload = dict(base_payload if base_payload is not None else task.payload)
        payload.pop("_auren_execution_context", None)
        if payload.get("auren_approval_aware") is True:
            payload["_auren_execution_context"] = {
                "task_id": task.id, "work_unit_id": task.work_unit_id, "attempt": task.attempts,
            }
        return payload

    def _verify_workflow_profile_binding(self, task: DurableTask) -> None:
        """Validate a task's persisted effective profile before any handler dispatch.

        Legacy tasks without either field remain on their original behavior. New
        tasks must carry both fields, and a partial, malformed, or digest-mismatched
        profile fails closed rather than silently adopting today's saved defaults.
        """
        profile_data = task.payload.get("_auren_workflow_profile")
        profile_digest = task.payload.get("_auren_workflow_profile_digest")
        if profile_data is None and profile_digest is None:
            return
        if not isinstance(profile_data, Mapping) or not isinstance(profile_digest, str):
            raise RuntimeError("durable workflow profile binding is incomplete")
        try:
            profile = WorkflowProfile.from_mapping(profile_data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("durable workflow profile is incompatible") from exc
        if profile.digest != profile_digest:
            raise RuntimeError("durable workflow profile digest mismatch")

    def _pause_for_approval(self, task: DurableTask, pending: ApprovalPending) -> str:
        """Persist a nonterminal approval wait and release the worker lease."""
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            approval = db.execute(
                "SELECT task_id,status,artifact_digest,evidence_digest,policy_digest,checkpoint_json,checkpoint_digest "
                "FROM durable_approvals WHERE approval_id=?",
                (pending.approval_id,),
            ).fetchone()
            row = db.execute(
                "SELECT payload,state,work_unit_id,lease_generation,lease_token FROM tasks WHERE id=?",
                (task.id,),
            ).fetchone()
            if approval is None or approval[0] != task.id:
                db.rollback()
                raise RuntimeError("approval request is missing or belongs to another task")
            if row is None or row[1] != "running" or row[4] != task.lease_token or int(row[3] or 0) != task.lease_generation:
                db.rollback()
                raise LeaseLostError("stale worker cannot pause a task for approval")
            stored_checkpoint = json.loads(approval[5])
            stored_checkpoint_digest = hashlib.sha256(
                json.dumps(stored_checkpoint, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
            if stored_checkpoint_digest != approval[6] or dict(pending.checkpoint) != stored_checkpoint:
                db.rollback()
                raise RuntimeError("approval continuation checkpoint differs from its durable digest-bound record")
            for key, value in (
                ("artifact_digest", approval[2]), ("evidence_digest", approval[3]),
                ("policy_digest", approval[4]),
            ):
                supplied = pending.checkpoint.get(key)
                if supplied is not None and supplied != value:
                    db.rollback()
                    raise RuntimeError(f"approval checkpoint {key} does not match the durable approval")
            status = str(approval[1])
            if status == "pending":
                next_state = "waiting_approval"
            elif status == "approved":
                next_state = "pending"
            else:
                next_state = "blocked_approval"
            payload = json.loads(row[0])
            prior = payload.get("auren_approval_resume")
            if isinstance(prior, Mapping) and prior.get("approval_id") != pending.approval_id:
                db.rollback()
                raise RuntimeError("task already has a different approval checkpoint")
            payload["auren_approval_resume"] = {
                "approval_id": pending.approval_id,
                "status": status,
                "checkpoint": dict(pending.checkpoint),
            }
            cursor = db.execute(
                "UPDATE tasks SET state=?,payload=?,updated_at=?,heartbeat_at=NULL,lease_until=NULL,"
                "lease_token=NULL WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (next_state, json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
                 _now(), task.id, task.lease_token, task.lease_generation),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise LeaseLostError("task changed while approval wait was being persisted")
            work_unit_id = str(row[2])
            db.commit()
        detail = f"Approval {pending.approval_id} is {status}; execution lease released."
        self.company.record(
            work_unit_id, "approval", next_state, detail,
            (f"task:{task.id}", f"approval:{pending.approval_id}"),
        )
        if next_state in {"waiting_approval", "blocked_approval"}:
            self.company._transition(work_unit_id, "waiting_external")
        self._capture_event("approval_waiting", task, status=next_state, metadata={
            "approval_id": pending.approval_id,
            "checkpoint_digest": hashlib.sha256(
                json.dumps(dict(pending.checkpoint), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
        })
        self._record_progress(
            task.id, "approval_requested" if next_state == "waiting_approval" else "approval_resume_ready",
            state="blocked" if next_state in {"waiting_approval", "blocked_approval"} else "queued",
            stage="approval", message=detail,
        )
        return next_state

    def resume_approved_task(self, task_id: str, approval_id: str, *, approval_store: Any) -> bool:
        """Wake the same task from its saved action checkpoint; duplicate wake-ups are harmless."""
        for value, name in ((task_id, "task_id"), (approval_id, "approval_id")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        approval = approval_store.get(approval_id)
        if approval.task_id != task_id or approval.status != "approved":
            raise PermissionError("only the matching durably approved operation can resume this task")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload,state,work_unit_id FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(task_id)
            if row[1] == "running":
                # The approval may arrive between durable request creation and the
                # handler raising ApprovalPending. The pause path will queue it.
                db.commit()
                return True
            payload = json.loads(row[0])
            checkpoint = payload.get("auren_approval_resume")
            if not isinstance(checkpoint, Mapping) or checkpoint.get("approval_id") != approval_id:
                db.rollback()
                raise RuntimeError("task does not contain the matching approval checkpoint")
            if row[1] == "pending" and checkpoint.get("status") == "approved":
                db.commit()
                return True
            if row[1] != "waiting_approval" or checkpoint.get("status") != "pending":
                db.rollback()
                raise InvalidApprovalTransition(f"task is {row[1]}, not waiting for this approval")
            payload["auren_approval_resume"] = {**dict(checkpoint), "status": "approved"}
            cursor = db.execute(
                "UPDATE tasks SET state='pending',payload=?,updated_at=?,heartbeat_at=NULL,lease_until=NULL,"
                "lease_token=NULL WHERE id=? AND state='waiting_approval'",
                (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False), _now(), task_id),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise RuntimeError("task state changed while applying approval wake-up")
            work_unit_id = str(row[2])
            db.commit()
        self.company.record(
            work_unit_id, "approval", "approved", "Approved action checkpoint queued for continuation.",
            (f"task:{task_id}", f"approval:{approval_id}"),
        )
        self._record_progress(
            task_id, "approval_approved", state="queued", stage="approval",
            message=f"Approval {approval_id} accepted; saved action checkpoint is ready to resume.",
        )
        return True

    def observe_failed_ci_check(self, *, recovery_ledger: Any, **observation: Any) -> Mapping[str, Any]:
        """Called by the existing supervisor's CI poll cycle; never starts another loop."""
        from .ci_repair_watchdog import CIRepairWatchdog
        return CIRepairWatchdog(self, recovery_ledger).observe_failed_check(**observation)

    def stale_work_findings(self, *, recovery_ledger: Any, stale_after_seconds: float,
                            now: float | None = None) -> list[Mapping[str, Any]]:
        """Expose bounded stale-heartbeat findings to the supervisor's existing cycle."""
        from .ci_repair_watchdog import CIRepairWatchdog
        return CIRepairWatchdog(self, recovery_ledger).stale_tasks(
            stale_after_seconds=stale_after_seconds, now=now,
        )

    def submit(
        self,
        handler: str,
        payload: Mapping[str, Any],
        *,
        goal: str | None = None,
        work_key: str | None = None,
        deadline_seconds: float | None = None,
    ) -> DurableTask:
        if deadline_seconds is not None and (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 86400
        ):
            raise ValueError("deadline_seconds must be finite, greater than 0, and at most 86400")
        requested_deadline_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=float(deadline_seconds))).isoformat()
            if deadline_seconds is not None else None
        )
        execution_class = self._handler_execution_class(handler)
        if not isinstance(payload, Mapping):
            raise TypeError("durable task payload must be a mapping")
        if execution_class == "context_provider":
            raise ValueError("context providers are internal-only and cannot be submitted as tasks")
        if execution_class not in {"read_only", "ci_gated", "consequential", "approval_gated"}:
            raise ValueError("unclassified legacy handler fails closed; re-register with an explicit execution_class")
        if execution_class == "approval_gated":
            if payload.get("auren_approval_aware") is not True:
                raise ValueError("approval-gated tasks must opt in to trusted execution context and checkpoint resume")
            if not self._handler_approval_gate_policy(handler):
                raise ValueError("approval-gated handler has no trusted gateway/ledger policy")
        self._load_handler(handler)
        payload_copy = dict(payload)
        # Never trust caller-supplied supervisor-owned profile fields. A public
        # workflow_profile may override saved preferences for this task only.
        task_profile = payload_copy.pop("workflow_profile", None)
        payload_copy.pop("_auren_workflow_profile", None)
        payload_copy.pop("_auren_workflow_profile_digest", None)
        saved_profile = load_profile(self.workflow_profile_path)
        effective_profile = resolve_profile(
            task=task_profile,
            saved=saved_profile.as_dict() if saved_profile is not None else None,
        )
        payload_copy["_auren_workflow_profile"] = effective_profile.as_dict()
        payload_copy["_auren_workflow_profile_digest"] = effective_profile.digest
        payload_key = payload_copy.get("work_key")
        candidate_key = work_key if work_key is not None else payload_key
        stable_work_key: str | None = None
        if candidate_key is not None:
            if (
                not isinstance(candidate_key, str)
                or not candidate_key.strip()
                or len(candidate_key.strip()) > 256
                or any(not char.isprintable() for char in candidate_key)
            ):
                raise ValueError("work_key must be a non-empty, printable string of at most 256 characters")
            stable_work_key = candidate_key.strip()
            if payload_key is not None and (
                not isinstance(payload_key, str) or payload_key.strip() != stable_work_key
            ):
                raise WorkKeyConflict("explicit work_key conflicts with payload work_key")
            # Keep the key visible in the durable task envelope so operators and
            # resumed handlers can identify the canonical work unit.
            payload_copy["work_key"] = stable_work_key
        # Assigned work must be host-authorized before its durable task intent
        # is created, not only when a worker later attempts dispatch. Keep the
        # same validation at dispatch time because registry or policy can change
        # between submission, restart, and execution.
        if "_auren_agent_assignment" in payload_copy:
            current_guard_policy = load_guard_policy(self.root / ".auren" / "guard_policy.json")
            assigned_resource = payload_copy.get(
                "resource", payload_copy.get("resource_id", payload_copy.get("path", ""))
            )
            validate_agent_assignment_for_dispatch(
                payload_copy,
                resource=assigned_resource,
                authorization_verifier=self._agent_assignment_verifier,
                expected_action_class=self._handler_agent_action_class(handler),
                expected_policy_version=POLICY_VERSION,
                expected_policy_digest=guard_policy_fingerprint(current_guard_policy),
            )
        goal_text = goal or f"execute durable handler: {handler}"
        normalized_deadline_seconds = (
            float(deadline_seconds) if deadline_seconds is not None else None
        )
        try:
            payload_json = json.dumps(
                payload_copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            )
            # Keep a legacy digest for safe reuse of pre-v2 tasks that had no
            # durable deadline policy. New task identity binds the relative
            # deadline policy, while the absolute deadline remains fixed at the
            # first successful submission and is never reset on replay.
            legacy_intent_json = json.dumps(
                {"handler": handler, "goal": goal_text, "payload": payload_copy},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            )
            intent_json = json.dumps(
                {
                    "schema_version": "auren-task-intent-v2",
                    "handler": handler,
                    "goal": goal_text,
                    "payload": payload_copy,
                    "deadline_policy_seconds": normalized_deadline_seconds,
                },
                sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("durable task payload and intent must be JSON-safe") from exc
        legacy_intent_digest = hashlib.sha256(legacy_intent_json.encode("utf-8")).hexdigest()
        intent_digest = hashlib.sha256(intent_json.encode("utf-8")).hexdigest()

        # Hold a SQLite write reservation across lookup, work-unit creation, and task
        # insertion. A committed task is then repaired/verified in the projection stores.
        task: DurableTask
        created = False
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = None
                if stable_work_key is not None:
                    existing = db.execute(
                        "SELECT id,handler,payload,state,attempts,work_unit_id,lease_generation,lease_token,intent_digest,deadline_at,deadline_seconds "
                        "FROM tasks WHERE work_key=?",
                        (stable_work_key,),
                    ).fetchone()
                if existing is not None:
                    stored_deadline_seconds = None
                    if existing[10] is not None:
                        try:
                            stored_deadline_seconds = float(existing[10])
                        except (TypeError, ValueError, OverflowError):
                            stored_deadline_seconds = float("nan")
                    deadline_policy_matches = (
                        stored_deadline_seconds is None and normalized_deadline_seconds is None
                    ) or (
                        stored_deadline_seconds is not None
                        and normalized_deadline_seconds is not None
                        and math.isfinite(stored_deadline_seconds)
                        and stored_deadline_seconds == normalized_deadline_seconds
                    )
                    legacy_reuse_is_safe = (
                        existing[8] == legacy_intent_digest
                        and stored_deadline_seconds is None
                        and normalized_deadline_seconds is None
                    )
                    if not (
                        (existing[8] == intent_digest and deadline_policy_matches)
                        or legacy_reuse_is_safe
                    ):
                        raise WorkKeyConflict(
                            "work_key already belongs to a different handler, payload, or goal; deadline policy must also match"
                        )
                    task = DurableTask(
                        existing[0], existing[1], json.loads(existing[2]), existing[3],
                        int(existing[4]), existing[5], int(existing[6] or 0), existing[7], existing[9],
                    )
                    db.commit()
                else:
                    work = self.company.start(goal_text, work_key=stable_work_key)
                    task = DurableTask(uuid4().hex, handler, payload_copy, "pending", 0, work.id, deadline_at=requested_deadline_at)
                    now = _now()
                    db.execute(
                        "INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,"
                        "heartbeat_at,lease_until,work_key,intent_digest,deadline_at,deadline_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            task.id, task.handler, payload_json, task.state, task.attempts, task.work_unit_id,
                            now, now, now, now, stable_work_key, intent_digest, requested_deadline_at,
                            normalized_deadline_seconds,
                        ),
                    )
                    db.commit()
                    created = True
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise

        if not self._ensure_submission_event(task):
            raise RuntimeError(
                "durable task exists but submission evidence could not be verified; "
                "retry with the same work_key and inspect controller health findings"
            )
        if created:
            self._record_progress(
                task.id, "task_submitted", state="queued", stage="queued",
                message="Task durably queued; execution has not started.",
            )
        try:
            has_progress = any(
                event.get("task_id") == task.id for event in self.progress_journal._events()
            )
        except Exception as exc:
            self._progress_journal_error = type(exc).__name__
            has_progress = False
        if not has_progress:
            self.reconcile_progress_journal(limit=1000, time_budget_seconds=2.0)
            try:
                has_progress = any(
                    event.get("task_id") == task.id for event in self.progress_journal._events()
                )
            except Exception as exc:
                self._progress_journal_error = type(exc).__name__
                has_progress = False
        if not has_progress:
            self._progress_journal_error = self._progress_journal_error or "submission_projection"
            raise RuntimeError(
                "durable task exists but progress projection could not be verified; "
                "retry with the same work_key and inspect controller health findings"
            )
        return task

    def request_cancellation(self, task_id: str, *, reason: str = "operator requested cancellation") -> bool:
        """Durably request cancellation; active handlers observe a live Event.

        Returns False for an already terminal or missing task. The request does
        not claim the task is cancelled: the owning worker must stop and commit
        the terminal state, or consequential work must remain blocked for
        reconciliation if the remote outcome is ambiguous.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("cancellation reason must be non-empty text of at most 1000 characters")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or row[0] in {"completed", "failed", "cancelled", "blocked_reconciliation"}:
                return False
            reason_digest = hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()
            db.execute(
                "UPDATE tasks SET cancel_requested=1, cancel_reason_digest=COALESCE(cancel_reason_digest, ?), "
                "updated_at=? WHERE id=? AND state IN ('pending','running')",
                (reason_digest, _now(), task_id),
            )
        with self._cancellation_events_lock:
            event = self._cancellation_events.get(task_id)
            if event is not None:
                event.set()
        self._record_progress(
            task_id, "cancellation_requested", state=str(row[0]), stage="cancellation",
            message="Cancellation requested; reason_digest=" + reason_digest
                    + ". Task outcome remains nonterminal until the owning worker confirms it.",
        )
        return True

    def _cancellation_requested(self, task_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db:
            row = db.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row[0])

    def _claim(self, task_id: str) -> DurableTask | None:
        # Claim under a write lock so two controllers cannot both observe
        # "pending" and execute the same side-effecting handler. Each claim
        # increments a fencing generation and receives a fresh unguessable token.
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id,lease_until,lease_generation,deadline_at "
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
        task = DurableTask(
            row[0], row[1], json.loads(row[2]), "running", attempts, row[5],
            lease_generation=generation, lease_token=token, deadline_at=row[8],
        )
        self._record_progress(
            task.id, "attempt_started", state="running", stage="dispatch",
            message=f"Worker claimed attempt {attempts}; preflight checks are next.",
        )
        return task

    def _finalize_claim(self, task: DurableTask, state: str) -> bool:
        if state not in {"completed", "failed", "cancelled", "blocked_reconciliation"}:
            raise ValueError("task final state is invalid")
        if not task.lease_token or task.lease_generation < 1:
            raise LeaseLostError("task claim has no fencing token")
        now = _now()
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            cursor = db.execute(
                "UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL,lease_token=NULL,"
                "quota_attempts=CASE WHEN ?='completed' THEN 0 ELSE quota_attempts END,"
                "quota_reason=CASE WHEN ?='completed' THEN NULL ELSE quota_reason END,"
                "quota_blocked=CASE WHEN ?='completed' THEN 0 ELSE quota_blocked END "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (state, now, now, state, state, state, task.id, task.lease_token, task.lease_generation),
            )
        if cursor.rowcount == 1:
            self._record_progress(
                task.id, "outcome_commit_started", state="running", stage="finalization",
                message="Task row updated; waiting for the executive work-unit receipt to commit.",
            )
            return True
        return False

    def _invoke_with_lease_heartbeat(self, task: DurableTask, handler: Handler) -> Any:
        """Renew leases and propagate durable cancellation while a handler runs."""
        stop = Event()
        cancel = Event()
        deadline = Event()
        lease_errors: list[BaseException] = []
        cancellation_observation_errors: list[BaseException] = []
        handler_started = False
        with self._cancellation_events_lock:
            self._cancellation_events[task.id] = cancel

        def renew() -> None:
            while not stop.wait(self._LEASE_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self.heartbeat(task)
                except BaseException as exc:
                    lease_errors.append(exc)
                    stop.set()
                    return

        def watch_cancellation() -> None:
            while not stop.wait(0.05):
                try:
                    if task.deadline_at is not None:
                        due = datetime.fromisoformat(task.deadline_at)
                        if due.tzinfo is None:
                            deadline.set()
                            cancel.set()
                            return
                        if datetime.now(timezone.utc) >= due.astimezone(timezone.utc):
                            deadline.set()
                            cancel.set()
                            return
                    if self._cancellation_requested(task.id):
                        cancel.set()
                        return
                except Exception as exc:
                    # A database read failure is not evidence of cancellation.
                    # Record uncertainty and fail closed after handler return.
                    cancellation_observation_errors.append(exc)
                    return

        lease_thread = Thread(target=renew, name=f"auren-lease-{task.id[:8]}", daemon=True)
        cancel_thread = Thread(target=watch_cancellation, name=f"auren-cancel-{task.id[:8]}", daemon=True)
        lease_thread.start()
        cancel_thread.start()
        result: Any = None
        handler_error: BaseException | None = None
        try:
            if task.deadline_at is not None:
                try:
                    due = datetime.fromisoformat(task.deadline_at)
                    if due.tzinfo is None:
                        raise ValueError("task deadline must be timezone-aware")
                except (TypeError, ValueError) as exc:
                    deadline.set()
                    raise TaskDeadlineExceeded("task deadline record is invalid") from exc
                if datetime.now(timezone.utc) >= due.astimezone(timezone.utc):
                    deadline.set()
                    raise TaskDeadlineExceeded("task deadline expired before handler dispatch")
            if self._cancellation_requested(task.id):
                cancel.set()
            if cancel.is_set():
                if deadline.is_set():
                    raise TaskDeadlineExceeded("task deadline expired before handler dispatch")
                raise TaskCancellationRequested("task cancellation requested before handler dispatch")
            payload = self._handler_payload(task)
            payload["auren_cancel_event"] = cancel
            if task.deadline_at is not None:
                payload["auren_deadline_epoch"] = datetime.fromisoformat(task.deadline_at).timestamp()
            # Revalidate the durable assignment immediately before entering the
            # handler and bind its verified exact-path scope to guarded adapters.
            resource = payload.get("resource", payload.get("resource_id", payload.get("path", "")))
            current_policy = load_guard_policy(self.root / ".auren" / "guard_policy.json")
            assignment_scope = validate_agent_assignment_for_dispatch(
                payload, resource=resource,
                authorization_verifier=self._agent_assignment_verifier,
                expected_action_class=self._handler_agent_action_class(task.handler),
                expected_policy_version=POLICY_VERSION,
                expected_policy_digest=guard_policy_fingerprint(current_policy),
            )
            handler_started = True
            with agent_assignment_scope(assignment_scope):
                result = handler(payload)
        except BaseException as exc:
            handler_error = exc
        finally:
            stop.set()
            lease_thread.join(timeout=2)
            cancel_thread.join(timeout=2)
            with self._cancellation_events_lock:
                self._cancellation_events.pop(task.id, None)
        if lease_errors:
            raise LeaseLostError(
                "task lease was lost while handler was running; result cannot be committed"
            ) from (handler_error or lease_errors[0])
        if handler_error is not None:
            if deadline.is_set():
                if handler_started and self._handler_execution_class(task.handler) == "consequential":
                    raise ProviderOutcomeBlocked("deadline expired after consequential dispatch; remote outcome requires reconciliation") from handler_error
                raise TaskDeadlineExceeded("task deadline expired during handler execution") from handler_error
            if cancel.is_set():
                if handler_started and self._handler_execution_class(task.handler) == "consequential":
                    raise ProviderOutcomeBlocked("consequential handler was interrupted after cancellation; remote outcome requires reconciliation") from handler_error
                raise TaskCancellationRequested("handler was interrupted after supervisor cancellation") from handler_error
            if handler_started and self._handler_execution_class(task.handler) == "consequential":
                raise ProviderOutcomeBlocked("consequential handler exited with an exception after dispatch; remote outcome requires reconciliation") from handler_error
            raise handler_error
        if cancellation_observation_errors:
            if handler_started and self._handler_execution_class(task.handler) == "consequential":
                raise ProviderOutcomeBlocked("cancellation state could not be verified after consequential dispatch; remote outcome requires reconciliation") from cancellation_observation_errors[0]
            raise RuntimeError("cancellation state could not be verified; handler result cannot be committed") from cancellation_observation_errors[0]
        if task.deadline_at is not None:
            try:
                final_deadline = datetime.fromisoformat(task.deadline_at)
                if final_deadline.tzinfo is None:
                    deadline.set()
                elif datetime.now(timezone.utc) >= final_deadline.astimezone(timezone.utc):
                    deadline.set()
            except (TypeError, ValueError):
                deadline.set()
        if deadline.is_set():
            if handler_started and self._handler_execution_class(task.handler) == "consequential":
                raise ProviderOutcomeBlocked("deadline expired after consequential dispatch; remote outcome requires reconciliation")
            raise TaskDeadlineExceeded("task deadline expired; handler result cannot be committed")
        if cancel.is_set() or self._cancellation_requested(task.id):
            if handler_started and self._handler_execution_class(task.handler) == "consequential":
                raise ProviderOutcomeBlocked("cancellation arrived after consequential dispatch; remote outcome requires reconciliation")
            raise TaskCancellationRequested("task cancellation requested; handler result cannot be committed")
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
            except (ValueError, OverflowError):
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
            cursor = db.execute(
                "UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL,lease_token=NULL,"
                "quota_provider=?,quota_reason=?,quota_attempts=?,next_retry_at=?,quota_blocked=? "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (state, now_dt.isoformat(), now_dt.isoformat(), provider[:100], detail,
                 quota_attempts, retry_at.isoformat() if retry_at and not blocked else None,
                 int(blocked), task.id, task.lease_token, task.lease_generation),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError("task lease was lost while recording provider quota wait")
        self._capture_event(
            "task_quota_wait" if not blocked else "task_quota_blocked", task,
            status=state,
            metadata={
                "phase": "quota_recovery",
                "external_system": provider[:100] or "unknown",
                "result_category": state,
                "retry_action": "wait_until_reset" if not blocked else "operator_action_required",
            },
        )
        self._record_progress(
            task.id, "provider_quota_wait", state="blocked", stage="provider_quota",
            message=(
                "Provider quota/rate limit; bounded retry is pending."
                if not blocked else "Provider quota/rate limit; operator action is required."
            ),
        )
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            rows = db.execute(
                "SELECT id FROM tasks WHERE state='waiting_quota' AND next_retry_at IS NOT NULL "
                "AND next_retry_at<=? ORDER BY next_retry_at,created_at LIMIT ?",
                (current.astimezone(timezone.utc).isoformat(), limit),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def quota_status(self, task_id: str) -> dict[str, Any] | None:
        """Expose the durable wait/block reason without leaking provider exception text."""
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
        for task_id in woken_ids:
            self._record_progress(
                task_id, "quota_retry_queued", state="queued", stage="provider_quota",
                message="Quota wait expired; task returned to the durable queue.",
            )
        return woken_ids

    def unblock_quota_task(self, task_id: str, *, operator_reason: str) -> bool:
        """Explicitly release a permanently blocked quota task after operator action."""
        if not isinstance(operator_reason, str) or not operator_reason.strip():
            raise ValueError("operator_reason is required to unblock quota work")
        now = _now()
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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

    def _verify_engineering_plan_dispatch_checkpoint(self, task: DurableTask) -> None:
        """Revalidate trusted plan state immediately before a queued handler can run.

        Ordinary controller tasks are unaffected. Engineering-plan dispatch tasks
        require a registered recovery bridge, matching durable intent, intact
        binding digest, and the exact plan/run revision captured at enqueue.
        """
        metadata = task.payload.get("auren_dispatch")
        if metadata is None:
            return
        from .engineering_plan_recovery import EngineeringPlanCheckpointMismatch

        if not isinstance(metadata, Mapping):
            raise EngineeringPlanCheckpointMismatch("dispatch metadata is malformed")
        required = {
            "run_id", "task_id", "plan_digest", "run_revision", "source_revision",
            "packet_digest", "handler", "payload_digest", "dispatch_key",
            "binding_digest", "plan_store_path",
        }
        if not required.issubset(metadata):
            raise EngineeringPlanCheckpointMismatch("dispatch metadata is incomplete")
        store_path = metadata.get("plan_store_path")
        bridge = self._engineering_plan_recovery_bridges.get(store_path)
        if bridge is None:
            raise EngineeringPlanCheckpointMismatch(
                "canonical recovery bridge is not registered in this worker; refusing task execution"
            )
        run_id = metadata.get("run_id")
        plan, run = bridge.load_verified(run_id)
        trusted_binding_fields = {
            "deadline_policy_seconds", "shared_budget_binding", "approval_evidence",
        }
        if (
            (
                getattr(self, "_trusted_plan_dispatch_required", False)
                or bool(trusted_binding_fields.intersection(metadata))
            )
            and getattr(self, "_trusted_plan_dispatch_token", None) is None
        ):
            raise EngineeringPlanCheckpointMismatch(
                "trusted plan dispatch root is not configured in this worker; refusing task execution"
            )
        if (
            plan.digest != metadata.get("plan_digest")
            or plan.source_revision != metadata.get("source_revision")
            or type(metadata.get("run_revision")) is not int
            or run.revision != metadata.get("run_revision")
        ):
            raise EngineeringPlanCheckpointMismatch(
                "queued dispatch no longer matches the canonical plan/run revision"
            )
        plan_task_id = metadata.get("task_id")
        progress = next((item for item in run.tasks if item.task_id == plan_task_id), None)
        if progress is None or progress.status not in {"ready", "running", "verifying"}:
            raise EngineeringPlanCheckpointMismatch(
                "queued dispatch plan task is no longer in a dispatchable state"
            )
        dispatch_key = metadata.get("dispatch_key")
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            intent = db.execute(
                "SELECT * FROM engineering_dispatch_intents WHERE dispatch_key=?",
                (dispatch_key,),
            ).fetchone()
        if intent is None or (
            intent["task_id"] != task.id
            or intent["run_id"] != run_id
            or intent["plan_task_id"] != plan_task_id
            or intent["plan_digest"] != plan.digest
            or int(intent["run_revision"]) != run.revision
            or intent["source_revision"] != plan.source_revision
            or intent["packet_digest"] != metadata.get("packet_digest")
            or task.handler != metadata.get("handler")
        ):
            raise EngineeringPlanCheckpointMismatch(
                "durable dispatch intent does not match the queued task binding"
            )
        safe_payload = dict(task.payload)
        safe_payload.pop("auren_dispatch", None)
        payload_digest = hashlib.sha256(
            json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        binding = {
            "run_id": run_id,
            "task_id": plan_task_id,
            "plan_digest": plan.digest,
            "run_revision": run.revision,
            "source_revision": plan.source_revision,
            "packet_digest": metadata.get("packet_digest"),
            "handler": task.handler,
            "payload_digest": payload_digest,
        }
        # New dispatch intents also bind the host-resolved budget and approval
        # receipts. Recompute that exact shape at worker preflight so a trusted-root
        # dispatch is not rejected before its handler starts. Preserve the historical
        # digest shape for older intents that predate these optional fields.
        extension_fields = {
            "deadline_policy_seconds", "shared_budget_binding", "approval_evidence",
        }
        present_extension_fields = extension_fields.intersection(metadata)
        if present_extension_fields and present_extension_fields != extension_fields:
            raise EngineeringPlanCheckpointMismatch(
                "queued dispatch has an incomplete budget/approval binding"
            )
        if present_extension_fields:
            deadline_policy_seconds = metadata.get("deadline_policy_seconds")
            if deadline_policy_seconds is not None:
                if (
                    isinstance(deadline_policy_seconds, bool)
                    or not isinstance(deadline_policy_seconds, (int, float))
                    or not math.isfinite(deadline_policy_seconds)
                    or not 0 < deadline_policy_seconds <= 86400
                ):
                    raise EngineeringPlanCheckpointMismatch(
                        "queued dispatch deadline binding is invalid"
                    )
                deadline_policy_seconds = float(deadline_policy_seconds)
            from .engineering_plan_dispatch import EngineeringPlanDispatch
            try:
                shared_budget_binding = EngineeringPlanDispatch._normalize_budget_binding(
                    metadata.get("shared_budget_binding")
                )
                approval_evidence = EngineeringPlanDispatch._normalize_approval_evidence(
                    metadata.get("approval_evidence")
                )
            except (TypeError, ValueError) as exc:
                raise EngineeringPlanCheckpointMismatch(
                    "queued dispatch budget or approval binding is invalid"
                ) from exc
            binding.update({
                "deadline_policy_seconds": deadline_policy_seconds,
                "shared_budget_binding": shared_budget_binding,
                "approval_evidence": approval_evidence,
            })
        binding_digest = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if (
            payload_digest != metadata.get("payload_digest")
            or binding_digest != metadata.get("binding_digest")
        ):
            raise EngineeringPlanCheckpointMismatch(
                "queued dispatch payload or binding digest is invalid"
            )

    def _invoke_consequential_with_ledger(self, task: DurableTask, handler: Handler) -> Any:
        """Run consequential handlers behind durable idempotency and Guard binding.

        The current organization policy is re-evaluated at dispatch. The decision
        digest and trusted registered action are included in the ledger binding so
        policy changes cannot silently replay an operation under different authority.
        """
        guard_decision = self._evaluate_guard_for_task(task)
        if not guard_decision.allowed:
            raise RuntimeError("organization Guard denied consequential dispatch: " + guard_decision.reason)
        guard_action = infer_delivery_action(task.handler, {}) or self._handler_guard_action(task.handler)
        guard_binding = {
            "action": guard_action,
            "policy_digest": guard_decision.policy_digest,
            "effect": guard_decision.effect,
            "matched_rule_ids": list(guard_decision.matched_rule_ids),
        }
        dispatch = task.payload.get("auren_dispatch")
        idempotency = task.payload.get("auren_idempotency")
        engineering_key = (
            dispatch.get("dispatch_key")
            if isinstance(dispatch, Mapping)
            else None
        )
        provider_neutral_key = (
            idempotency.get("key")
            if isinstance(idempotency, Mapping)
            else None
        )
        if isinstance(engineering_key, str) and engineering_key.strip():
            stable_key = engineering_key.strip()
            key = "engineering-plan:" + stable_key
            identity_kind = "engineering_dispatch"
        elif isinstance(provider_neutral_key, str) and provider_neutral_key.strip():
            stable_key = provider_neutral_key.strip()
            key = "consequential-idempotency:" + stable_key
            identity_kind = "consequential_idempotency"
        else:
            stable_key = None
            key = "execution-task:" + task.id
            identity_kind = "execution_task"
        safe_payload = dict(task.payload)
        safe_payload.pop("auren_dispatch", None)
        safe_payload.pop("auren_idempotency", None)
        payload_digest = hashlib.sha256(
            json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        # Stable keys permit safe receipt replay/reconciliation across recreated task
        # IDs. The caller must supply a stable key only for the same logical operation;
        # provider-native idempotency and history lookup are still adapter-specific.
        binding = {
            "identity": (
                {"kind": identity_kind, "key": stable_key}
                if stable_key is not None
                else {"kind": "execution_task", "task_id": task.id}
            ),
            "handler": task.handler,
            "payload_digest": payload_digest,
            "guard": guard_binding,
        }
        decision = DurableSideEffectLedger(self.db_path).begin(key, binding)
        if decision.action == "replay":
            receipt = decision.receipt
            if not isinstance(receipt, Mapping) or not isinstance(receipt.get("result"), Mapping):
                raise RuntimeError("consequential side-effect receipt is malformed; refusing handler replay")
            return dict(receipt["result"])
        if decision.action != "execute":
            adapter = self._provider_operation_adapters.get(task.handler)
            if decision.state != "reconcile_required":
                raise RuntimeError(
                    "consequential side effect requires reconciliation; provider dispatch is blocked "
                    f"(ledger_state={decision.state or 'unknown'})"
                )
            if adapter is None:
                raise ProviderOutcomeBlocked(
                    "provider outcome remains uncertain; task is blocked for explicit operator resolution"
                )
            lookup_fn = getattr(adapter, "lookup_operation", None)
            verify_fn = getattr(adapter, "verify_reconciliation", None)
            if not callable(lookup_fn) or not callable(verify_fn):
                raise RuntimeError("provider adapter reconciliation capabilities are incomplete")
            from .provider_operation_lifecycle import (
                ProviderOperationLifecycle, stable_provider_operation_id,
            )
            provider_name = task.handler
            provider_intent = {"binding": binding, "payload": safe_payload}
            operation_key = "provider-operation:" + key
            operation_id = stable_provider_operation_id(operation_key, provider_name, provider_intent)
            def record_provider_event(event: Mapping[str, Any]) -> None:
                operation_state = str(event.get("state", "unknown"))
                safe_metadata = {
                    "operation_id": str(event.get("operation_id", "")),
                    "intent_digest": str(event.get("intent_digest", "")),
                    "provider": provider_name,
                    "operation_state": operation_state,
                    "guard_action": guard_action or "unclassified",
                    "guard_policy_digest": guard_decision.policy_digest,
                    "guard_decision": guard_decision.effect,
                    "guard_rule_ids": ",".join(guard_decision.matched_rule_ids) or "none",
                }
                if hasattr(task, "work_unit_id") and hasattr(task, "attempts"):
                    self._capture_event(
                        "provider_operation_" + operation_state, task,
                        status="unknown", metadata=safe_metadata,
                    )
                self._record_progress(
                    task.id, "provider_operation_" + operation_state,
                    state="running", stage="provider_operation",
                    message="Provider operation state: " + operation_state + " (" + operation_id + ")",
                    evidence_ref=None,
                )

            provider_ledger = DurableSideEffectLedger(
                self.db_path, reconciliation_verifier=verify_fn,
            )
            lifecycle = ProviderOperationLifecycle(
                provider_ledger, record_event=record_provider_event,
            )
            existing_provider_record = provider_ledger.get(operation_key)
            if existing_provider_record is not None and existing_provider_record.get("state") == "completed":
                # The provider receipt was committed before a prior worker died.
                # Reuse the digest-bound durable receipt; remote lookup is unnecessary.
                provider_receipt = lifecycle.reconcile(
                    operation_key, provider_name, provider_intent, None,
                )
                evidence_ref = provider_receipt.get("evidence_ref")
                if not isinstance(evidence_ref, str) or not evidence_ref.strip():
                    raise ProviderOutcomeBlocked(
                        "durable provider receipt lacks its evidence reference; task remains blocked"
                    )
            else:
                try:
                    lookup_result = lookup_fn(operation_id, operation_key)
                except Exception as exc:
                    raise ProviderOutcomeBlocked(
                        f"provider operation {operation_id} lookup failed; explicit resolution required"
                    ) from exc
                if (
                    not isinstance(lookup_result, Mapping)
                    or lookup_result.get("status") != "completed"
                    or not isinstance(lookup_result.get("evidence_ref"), str)
                    or not lookup_result.get("evidence_ref", "").strip()
                    or not isinstance(lookup_result.get("receipt"), Mapping)
                ):
                    raise ProviderOutcomeBlocked(
                        f"provider operation {operation_id} outcome is not established; task remains blocked"
                    )
                evidence_ref = lookup_result["evidence_ref"]
                provider_receipt = lifecycle.reconcile(
                    operation_key, provider_name, provider_intent,
                    lambda _operation_id, _idempotency_key: lookup_result,
                )
            result = provider_receipt.get("result")
            if not isinstance(result, Mapping):
                raise ProviderOutcomeBlocked("reconciled provider receipt is malformed; task remains blocked")
            outer_receipt = {
                "result": dict(result),
                "provider_operation_id": provider_receipt.get("provider_operation_id"),
                "provider_receipt": dict(provider_receipt),
            }

            def verify_outer(_key: str, ref: str, receipt: Mapping[str, Any]) -> bool:
                nested = receipt.get("provider_receipt")
                if not isinstance(nested, Mapping) or ref != evidence_ref:
                    return False
                return verify_fn(operation_key, ref, nested) is True

            DurableSideEffectLedger(
                self.db_path, reconciliation_verifier=verify_outer,
            ).record_reconciled_effect(key, evidence_ref=evidence_ref, receipt=outer_receipt)
            return dict(result)
        # Keep provider identity and operation identity in the same canonical
        # ledger, while the outer row remains the supervisor's task-level gate.
        from .provider_operation_lifecycle import ProviderOperationLifecycle

        provider_name = task.handler
        provider_intent = {"binding": binding, "payload": safe_payload}
        def record_provider_event(event: Mapping[str, Any]) -> None:
            operation_state = str(event.get("state", "unknown"))
            operation_id = str(event.get("operation_id", ""))
            safe_metadata = {
                "operation_id": operation_id,
                "intent_digest": str(event.get("intent_digest", "")),
                "provider": provider_name,
                "operation_state": operation_state,
                    "guard_action": guard_action or "unclassified",
                    "guard_policy_digest": guard_decision.policy_digest,
                    "guard_decision": guard_decision.effect,
                    "guard_rule_ids": ",".join(guard_decision.matched_rule_ids) or "none",
            }
            if hasattr(task, "work_unit_id") and hasattr(task, "attempts"):
                self._capture_event("provider_operation_" + operation_state, task,
                                    status="unknown", metadata=safe_metadata)
            self._record_progress(
                task.id, "provider_operation_" + operation_state,
                state="running", stage="provider_operation",
                message="Provider operation state: " + operation_state + " (" + operation_id + ")",
                evidence_ref=None,
            )

        provider_ledger = DurableSideEffectLedger(self.db_path)
        lifecycle = ProviderOperationLifecycle(
            provider_ledger, record_event=record_provider_event,
        )
        def dispatch_provider(intent: Mapping[str, Any], operation_id: str, idempotency_key: str) -> Mapping[str, Any]:
            latest_guard = self._evaluate_guard_for_task(task)
            if (
                not latest_guard.allowed
                or latest_guard.policy_digest != guard_decision.policy_digest
                or latest_guard.effect != guard_decision.effect
                or latest_guard.matched_rule_ids != guard_decision.matched_rule_ids
            ):
                raise RuntimeError("Guard policy changed or denied the action before provider dispatch")
            provider_payload = dict(task.payload)
            provider_payload["auren_provider_operation"] = {
                "operation_id": operation_id, "idempotency_key": idempotency_key,
                "provider": provider_name,
                "guard_action": guard_action or "unclassified",
                "guard_policy_digest": guard_decision.policy_digest,
                "guard_decision": guard_decision.effect,
                "guard_rule_ids": ",".join(guard_decision.matched_rule_ids) or "none",
                "intent_digest": hashlib.sha256(
                    json.dumps(dict(intent), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                ).hexdigest(),
            }
            def invoke_provider_handler(runtime_payload):
                handler_payload = {
                    **provider_payload,
                    "auren_cancel_event": runtime_payload["auren_cancel_event"],
                }
                if "auren_deadline_epoch" in runtime_payload:
                    handler_payload["auren_deadline_epoch"] = runtime_payload["auren_deadline_epoch"]
                return handler(self._handler_payload(task, handler_payload))

            value = self._invoke_with_lease_heartbeat(task, invoke_provider_handler)
            if not isinstance(value, Mapping):
                raise RuntimeError("consequential handlers must return a mapping receipt/result")
            intent_digest = provider_ledger.digest_payload({
                "provider": provider_name,
                "operation_id": operation_id,
                "intent": dict(intent),
            })
            return {
                "status": "completed",
                "result": dict(value),
                "provider_operation_id": operation_id,
                "operation_id": operation_id,
                "idempotency_key": idempotency_key,
                "provider": provider_name,
                "intent_digest": intent_digest,
            }

        provider_receipt = lifecycle.execute(
            "provider-operation:" + key, provider_name, provider_intent, dispatch_provider,
        )
        result = provider_receipt.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("provider operation receipt is malformed; task remains blocked")
        receipt = {"result": dict(result), "provider_operation_id": provider_receipt.get("provider_operation_id")}
        DurableSideEffectLedger(self.db_path).complete(key, receipt)
        return dict(result)

    def run_once(self, task_id: str) -> TrustScore | None:
        task = self._claim(task_id)
        if task is None:
            return None
        self._capture_event("task_attempt_started", task, status="running", metadata={
            "handler": task.handler,
        })
        self._record_progress(
            task.id, "preflight_started", state="running", stage="preflight",
            message="Checking dispatch policy, revision binding, and required evidence.",
        )
        try:
            if self._cancellation_requested(task.id):
                raise TaskCancellationRequested("task cancellation requested before preflight")
            if task.deadline_at is not None:
                try:
                    deadline = datetime.fromisoformat(task.deadline_at)
                except (TypeError, ValueError) as exc:
                    raise TaskDeadlineExceeded("task deadline record is invalid") from exc
                if deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc):
                    raise TaskDeadlineExceeded("task deadline expired before preflight")
            self.heartbeat(task)
            self._verify_engineering_plan_dispatch_checkpoint(task)
            self._verify_workflow_profile_binding(task)
            gates_passed, gate_detail, gate_evidence = self._preflight_gates(task)
            if not gates_passed:
                if not self._finalize_claim(task, "failed"):
                    raise LeaseLostError("stale worker cannot finalize rejected task")
                evidence = [f"handler:{task.handler}", *gate_evidence]
                self.company.record(task.work_unit_id, "execution", "failed", gate_detail, evidence)
                score = self.company.finish(task.work_unit_id, success=False, reason=gate_detail, evidence=evidence)
                self._capture_terminal_event("task_failed", task, status="failed", metadata={
                    "handler": task.handler, "phase": "preflight", "failure_class": "gate_rejection",
                })
                self._record_progress(
                    task.id, "task_terminal", state="failed", stage="terminal",
                    message="Preflight gate rejected the task; failure receipt committed.",
                )
                return score

            self._record_progress(
                task.id, "handler_started", state="running", stage="handler",
                message="Preflight passed; handler dispatch is starting.",
            )
            handler = self._load_handler(task.handler)
            execution_class = self._handler_execution_class(task.handler)
            # Close the preflight-to-dispatch policy-change window for every
            # registered handler, not only consequential provider operations.
            try:
                dispatch_guard = self._evaluate_guard_for_task(task)
            except (GuardPolicyError, OSError, ValueError) as exc:
                raise RuntimeError(
                    "organization Guard could not be verified immediately before handler dispatch"
                ) from exc
            if not dispatch_guard.allowed:
                raise RuntimeError(
                    "organization Guard denied handler dispatch: " + dispatch_guard.reason
                )
            # Persist the exact final policy decision before the handler can act.
            # Evidence failure raises here and therefore prevents dispatch.
            dispatch_action = infer_delivery_action(task.handler, {}) or self._handler_guard_action(task.handler)
            self._capture_event(
                "guard_dispatch_decision",
                task,
                status="allowed",
                metadata={
                    "guard_action": dispatch_action or "unclassified",
                    "guard_policy_digest": dispatch_guard.policy_digest,
                    "guard_decision": dispatch_guard.effect,
                    "guard_rule_ids": ",".join(dispatch_guard.matched_rule_ids) or "none",
                },
                event_id=f"guard-dispatch:{task.id}:{task.attempts}:{dispatch_guard.policy_digest}",
            )
            # Evidence capture is part of preflight, not permission to execute.
            # Recheck after receipt persistence so a policy replacement during
            # the write cannot turn a stale allow into a dispatch.
            try:
                final_guard = self._evaluate_guard_for_task(task)
            except (GuardPolicyError, OSError, ValueError) as exc:
                raise RuntimeError(
                    "organization Guard could not be verified at the final handler boundary"
                ) from exc
            if (
                not final_guard.allowed
                or final_guard.policy_digest != dispatch_guard.policy_digest
                or final_guard.effect != dispatch_guard.effect
                or final_guard.matched_rule_ids != dispatch_guard.matched_rule_ids
            ):
                raise RuntimeError(
                    "organization Guard changed after decision receipt; handler dispatch blocked"
                )
            if execution_class == "consequential":
                result = self._invoke_consequential_with_ledger(task, handler)
            else:
                result = self._invoke_with_lease_heartbeat(task, handler)
            if execution_class == "approval_gated":
                self._verify_approval_gated_result(task, result)
        except LeaseLostError:
            raise
        except ApprovalPending as exc:
            self._pause_for_approval(task, exc)
            return None
        except TaskDeadlineExceeded as exc:
            if not self._finalize_claim(task, "failed"):
                raise LeaseLostError("task lease was lost; stale worker cannot finalize deadline failure") from exc
            detail = "Task deadline exceeded; handler result was not accepted."
            self.company.record(task.work_unit_id, "execution", "failed", detail,
                                (f"handler:{task.handler}", f"task:{task.id}", "deadline_exceeded"))
            self.company.finish(task.work_unit_id, success=False, reason=detail)
            self._capture_terminal_event("task_failed", task, status="failed", metadata={
                "handler": task.handler, "phase": "deadline",
                "failure_class": "TaskDeadlineExceeded",
            })
            self._record_progress(task.id, "task_terminal", state="failed", stage="terminal", message=detail)
            return None
        except TaskCancellationRequested as exc:
            if not self._finalize_claim(task, "cancelled"):
                raise LeaseLostError(
                    "task lease was lost; stale worker cannot finalize cancellation"
                ) from exc
            detail = "Task cancelled by supervisor request; handler result was not accepted."
            self.company.record(
                task.work_unit_id, "execution", "failed", detail,
                (f"handler:{task.handler}", f"task:{task.id}", "cancellation_requested"),
            )
            self.company.finish(task.work_unit_id, success=False, reason=detail)
            self._capture_terminal_event("task_cancelled", task, status="cancelled", metadata={
                "handler": task.handler, "phase": "cancellation",
                "failure_class": "TaskCancellationRequested",
            })
            self._record_progress(
                task.id, "task_terminal", state="cancelled", stage="terminal",
                message=detail,
            )
            return None
        except ProviderOutcomeBlocked as exc:
            if not self._finalize_claim(task, "blocked_reconciliation"):
                raise LeaseLostError(
                    "task lease was lost; stale worker cannot finalize an ambiguous provider outcome"
                ) from exc
            detail = f"Provider outcome is uncertain; explicit resolution required: {exc}"
            self._capture_event("provider_operation_blocked", task, status="unknown", metadata={
                "handler": task.handler, "failure_class": "provider_outcome_uncertain",
            })
            self.company.record(
                task.work_unit_id, "provider_operation", "blocked", detail,
                (f"handler:{task.handler}", f"task:{task.id}"),
            )
            self.company._transition(task.work_unit_id, "waiting_external")
            clarification_id = self.company.hold_clarification(
                task.work_unit_id,
                "Provider outcome cannot be established automatically. Verify the remote operation before resuming.",
                dependency=f"provider-operation-task:{task.id}",
                evidence=(f"task:{task.id}",),
            )
            self._record_progress(
                task.id, "provider_operation_blocked", state="blocked", stage="provider_operation",
                message=detail + f" Clarification required: {clarification_id}",
            )
            return None
        except Exception as exc:
            if self._pause_for_quota(task, exc):
                return None
            if not self._finalize_claim(task, "failed"):
                raise LeaseLostError(
                    "task lease was lost; stale worker cannot overwrite the current task outcome"
                ) from exc
            detail = f"{type(exc).__name__}: {exc}"
            self.company.record(task.work_unit_id, "execution", "failed", detail, (f"handler:{task.handler}",))
            self.company.finish(task.work_unit_id, success=False, reason=detail)
            self._capture_terminal_event("task_failed", task, status="failed", metadata={
                "handler": task.handler, "phase": "exception",
                "failure_class": type(exc).__name__,
            })
            self._record_progress(
                task.id, "task_terminal", state="failed", stage="terminal",
                message="Handler failed; durable failure receipt committed.",
            )
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
        self.company.record(task.work_unit_id, "execution", state, detail, evidence)
        score = self.company.finish(task.work_unit_id, success=success, reason=detail, evidence=evidence)
        self._capture_terminal_event(
            "task_completed" if success else "task_failed",
            task,
            status=state,
            metadata={
                "handler": task.handler, "phase": "handler",
                "result_category": "success" if success else "failure",
            },
        )
        self._record_progress(
            task.id, "task_terminal", state=state, stage="terminal",
            message=f"Supervisor committed terminal state and work-unit receipt: {state}.",
        )
        return score

    def resume_pending(
        self, *, limit: int = 20, continue_after_durable_failure: bool = False
    ) -> list[TrustScore]:
        """Resume eligible work without letting a live lease consume the scan budget.

        The default preserves fail-fast behavior for direct callers. A bounded
        service loop may continue after a handler exception only when both the task
        row and executive event ledger prove that the task reached terminal failure.
        Lease loss and partial persistence still propagate instead of being reported
        as ordinary task failures.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("resume limit must be a positive integer")
        if type(continue_after_durable_failure) is not bool:
            raise ValueError("continue_after_durable_failure must be a boolean")
        # Decisions can arrive while the worker is absent. Reconcile them on every
        # bounded supervisor cycle so rejected/expired approvals do not wait for restart.
        self.reconcile_approval_waits()
        self.wake_due_quota_tasks(limit=limit)
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.db_path)) as db, db:
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
            try:
                score = self.run_once(task_id)
            except LeaseLostError:
                raise
            except Exception:
                if not continue_after_durable_failure:
                    raise
                task = self.task(task_id)
                if task is None or task.state != "failed":
                    raise
                work_state, _ = self.company._state(task.work_unit_id)
                if work_state != "failed":
                    raise
                with closing(sqlite3.connect(self.company.db_path)) as db, db:
                    failure_event = db.execute(
                        "SELECT 1 FROM events WHERE work_unit_id=? "
                        "AND kind='execution' AND status='failed' LIMIT 1",
                        (task.work_unit_id,),
                    ).fetchone()
                if failure_event is None:
                    raise
                # The handler exception is reportable only after both durable
                # ledgers confirm failure. Do not retry or replay the failed task.
                score = self.company.trust(task.work_unit_id)
            if score is not None:
                scores.append(score)
        return scores

    def run_continuously(
        self,
        *,
        stop_event: Event | None = None,
        batch_size: int = 20,
        idle_sleep_seconds: float = 1.0,
        max_cycles: int = 100,
        max_idle_cycles: int = 5,
    ) -> ContinuousRunReport:
        """Run bounded resume batches until cancellation, cycle budget, or idle stop.

        State remains in the durable task/work-unit ledgers between calls. This
        service loop never interprets an empty batch as success and exits after a
        configurable no-progress budget so a scheduler can safely resume it later.
        Cancellation is observed between batches; handlers must use the existing
        lease heartbeat/cancellation contracts for work already in progress.
        """
        for value, name in ((batch_size, "batch_size"), (max_cycles, "max_cycles"), (max_idle_cycles, "max_idle_cycles")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if batch_size > 100 or max_cycles > 1000 or max_idle_cycles > 100:
            raise ValueError("continuous loop budgets exceed hard limits (batch_size<=100, max_cycles<=1000, max_idle_cycles<=100)")
        if max_idle_cycles > max_cycles:
            raise ValueError("max_idle_cycles cannot exceed max_cycles")
        if (
            isinstance(idle_sleep_seconds, bool)
            or not isinstance(idle_sleep_seconds, (int, float))
            or not math.isfinite(idle_sleep_seconds)
            or not 0 <= idle_sleep_seconds <= 300
        ):
            raise ValueError("idle_sleep_seconds must be finite and between 0 and 300 seconds")
        event = stop_event if stop_event is not None else Event()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            before_states = {
                str(task_id): str(state)
                for task_id, state in db.execute("SELECT id,state FROM tasks").fetchall()
            }
        scores: list[TrustScore] = []
        cycles = 0
        idle_cycles = 0
        stop_reason = "cycle_budget_exhausted"
        while cycles < max_cycles:
            if event.is_set():
                stop_reason = "cancelled"
                break
            cycles += 1
            batch = self.resume_pending(
                limit=batch_size, continue_after_durable_failure=True,
            )
            if batch:
                scores.extend(batch)
                idle_cycles = 0
                continue
            idle_cycles += 1
            if idle_cycles >= max_idle_cycles:
                stop_reason = "idle_budget_exhausted"
                break
            if event.wait(idle_sleep_seconds):
                stop_reason = "cancelled"
                break
        else:
            stop_reason = "cycle_budget_exhausted"
        if event.is_set() and stop_reason != "idle_budget_exhausted":
            stop_reason = "cancelled"
        with closing(sqlite3.connect(self.db_path)) as db, db:
            after_states = {
                str(task_id): str(state)
                for task_id, state in db.execute("SELECT id,state FROM tasks").fetchall()
            }
        remaining_nonterminal = sum(
            state not in {"completed", "failed", "cancelled"}
            for state in after_states.values()
        )
        state_counts: dict[str, int] = {}
        for state in after_states.values():
            state_counts[state] = state_counts.get(state, 0) + 1
        completed = sum(
            state == "completed" and before_states.get(task_id) != "completed"
            for task_id, state in after_states.items()
        )
        failed = sum(
            state == "failed" and before_states.get(task_id) != "failed"
            for task_id, state in after_states.items()
        )
        return ContinuousRunReport(
            cycles=cycles, processed_work_units=len(scores),
            completed_tasks=completed, failed_tasks=failed,
            remaining_nonterminal_tasks=remaining_nonterminal,
            idle_cycles=idle_cycles, stop_reason=stop_reason, trust_scores=tuple(scores),
            task_state_counts=tuple(sorted(state_counts.items())),
        )

    def heartbeat(self, task: DurableTask, *, lease_seconds: int = 300) -> None:
        if not isinstance(task, DurableTask) or not task.lease_token or task.lease_generation < 1:
            raise LeaseLostError("heartbeat requires the current durable task fencing token")
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=max(1, lease_seconds))).isoformat()
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            cursor = db.execute(
                "UPDATE tasks SET heartbeat_at=?,lease_until=?,updated_at=? "
                "WHERE id=? AND state='running' AND lease_token=? AND lease_generation=?",
                (now.isoformat(), lease, now.isoformat(), task.id, task.lease_token, task.lease_generation),
            )
        if cursor.rowcount != 1:
            raise LeaseLostError("task lease was lost; stale worker cannot renew it")
        # Receiver-acknowledged response delivery has its own journal state.
        # A generic lease heartbeat must not overwrite blocked/stale/terminal
        # delivery evidence with a misleading running projection.
        if task.handler != "portable.response_delivery.await_receiver":
            self._record_progress(
                task.id, "heartbeat", state="running", stage="execution",
                message="Worker lease renewed; completion is not implied.",
            )

    def detect_stale(self, stale_after_seconds: int = 900) -> list[DurableTask]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))).isoformat()
        with closing(sqlite3.connect(self.db_path)) as db, db:
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
            self._record_progress(
                recovered[0], "stale_task_recovering", state="recovering", stage="recovery",
                message="Stale lease expired; supervisor is reconciling the task before retry.",
            )
            self._record_progress(
                recovered[0], "stale_task_requeued", state="queued", stage="recovery",
                message="Recovery returned the task to the durable queue; completion is not implied.",
            )
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
        with closing(sqlite3.connect(self.db_path)) as db, db:
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

    def inspect_graph_checkpoints(
        self, *, limit: int = 1000, time_budget_seconds: float = 2.0
    ) -> dict[str, Any]:
        """Audit saved graph checkpoints without repairing or replacing damaged bytes.

        Checkpoint corruption must be visible before resume. This scan is bounded
        and read-only: an invalid checkpoint remains unusable until an operator
        restores trusted bytes and reconciles the associated task/evidence.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("checkpoint audit limit must be an integer from 1 to 10000")
        if (
            isinstance(time_budget_seconds, bool)
            or not isinstance(time_budget_seconds, (int, float))
            or not math.isfinite(time_budget_seconds)
            or not 0.05 <= time_budget_seconds <= 30.0
        ):
            raise ValueError("checkpoint audit time budget must be between 0.05 and 30 seconds")
        started = time.monotonic()
        deadline = started + float(time_budget_seconds)
        try:
            with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
                rows = db.execute(
                    "SELECT task_id,envelope_json,envelope_digest FROM graph_checkpoints "
                    "ORDER BY updated_at DESC,task_id LIMIT ?",
                    (limit + 1,),
                ).fetchall()
        except Exception as exc:
            self._graph_checkpoint_reconciliation_findings = ["checkpoint-store-unreadable"]
            self._graph_checkpoint_reconciliation_incomplete = True
            return {
                "scanned": 0, "valid": 0,
                "findings": [{"task_id": None, "reason": type(exc).__name__}],
                "incomplete": True,
                "next_action": "restore checkpoint-store access, then rerun the bounded audit; do not resume graph tasks while the store is unreadable",
            }

        scanned = 0
        findings: list[dict[str, str]] = []
        incomplete = len(rows) > limit
        rows = rows[:limit]
        for task_id, encoded, supplied_digest in rows:
            if time.monotonic() >= deadline:
                incomplete = True
                break
            scanned += 1
            try:
                if (
                    not isinstance(encoded, str)
                    or not isinstance(supplied_digest, str)
                    or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != supplied_digest
                ):
                    raise ValueError("stored graph checkpoint digest mismatch")
                envelope = json.loads(encoded)
                self._validate_graph_checkpoint_envelope(str(task_id), envelope)
            except Exception as exc:
                reason = (
                    "digest_mismatch"
                    if "digest mismatch" in str(exc).lower()
                    else type(exc).__name__
                )
                findings.append({"task_id": str(task_id), "reason": reason})

        if scanned < len(rows):
            incomplete = True
        self._graph_checkpoint_reconciliation_findings = [
            f"{item['task_id']}:{item['reason']}" for item in findings
        ]
        self._graph_checkpoint_reconciliation_incomplete = incomplete
        actions = []
        if findings:
            actions.append(
                "restore each affected checkpoint from trusted bytes and reconcile its task/evidence; do not replay the graph"
            )
        if incomplete:
            actions.append(
                "rerun inspect_graph_checkpoints with a larger bounded limit or time budget before trusting unscanned checkpoints"
            )
        return {
            "scanned": scanned,
            "valid": scanned - len(findings),
            "findings": findings,
            "incomplete": incomplete,
            "next_action": "; ".join(actions) if actions else None,
        }

    def health_findings(self) -> tuple[str, ...]:
        self.inspect_graph_checkpoints(limit=100, time_budget_seconds=0.2)
        findings = []
        with closing(sqlite3.connect(self.db_path)) as db, db:
            failed = db.execute("SELECT COUNT(*) FROM tasks WHERE state='failed'").fetchone()[0]
            blocked_quota = db.execute("SELECT COUNT(*) FROM tasks WHERE state='blocked_quota'").fetchone()[0]
            stale = db.execute(
                "SELECT COUNT(*) FROM tasks WHERE state='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)",
                ((datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),),
            ).fetchone()[0]
        if failed:
            findings.append(f"failed_tasks={failed}")
        if blocked_quota:
            findings.append(f"blocked_quota_tasks={blocked_quota}; operator action required")
        if stale:
            findings.append(f"stale_tasks={stale}")
        if self._progress_journal_error is not None:
            findings.append(
                "progress_journal_write_failed; task state remains in SQLite and progress visibility needs repair"
            )
        if self._progress_reconciliation_findings:
            findings.append(
                f"progress_reconciliation_required={len(self._progress_reconciliation_findings)}; "
                "journal reconciliation has unresolved store, lease, or journal-integrity findings; inspect before retry"
            )
        if self._progress_reconciliation_incomplete:
            findings.append("progress_reconciliation_incomplete; rerun reconciliation with a larger limit or time budget before trusting unscanned progress")
        if self._submission_reconciliation_findings:
            findings.append(
                f"submission_evidence_reconciliation_required={len(self._submission_reconciliation_findings)}; "
                "task submission evidence is missing or mismatched; inspect before treating the audit projection as complete"
            )
        if self._submission_reconciliation_incomplete:
            findings.append(
                "submission_evidence_reconciliation_incomplete; rerun bounded submission evidence repair before trusting unscanned tasks"
            )
        if self._terminal_reconciliation_findings:
            findings.append(
                f"terminal_evidence_reconciliation_required={len(self._terminal_reconciliation_findings)}; "
                "terminal task evidence is missing or mismatched; inspect before treating the audit projection as complete"
            )
        if self._terminal_reconciliation_incomplete:
            findings.append(
                "terminal_evidence_reconciliation_incomplete; rerun bounded terminal evidence repair before trusting unscanned tasks"
            )
        if self._graph_checkpoint_reconciliation_findings:
            findings.append(
                f"graph_checkpoint_integrity_required={len(self._graph_checkpoint_reconciliation_findings)}; "
                "saved supervisor graph checkpoint is corrupt or invalid; restore trusted bytes and reconcile before resume"
            )
        if self._graph_checkpoint_reconciliation_incomplete:
            findings.append(
                "graph_checkpoint_audit_incomplete; inspect unscanned graph checkpoints before trusting checkpoint health"
            )
        if self.evidence_capture.sink is not None:
            delivery = self.evidence_capture.flush_pending(limit=100)
            if delivery["pending"]:
                findings.append(f"evidence_exports_pending={delivery['pending']}")
        return tuple(findings)

    def task(self, task_id: str) -> DurableTask | None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute(
                "SELECT id,handler,payload,state,attempts,work_unit_id,deadline_at FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        return DurableTask(row[0], row[1], json.loads(row[2]), row[3], int(row[4]), row[5], deadline_at=row[6]) if row else None

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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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
        with closing(sqlite3.connect(self.evidence_capture.path, timeout=10)) as db, db:
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
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
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


__all__ = ["ExecutionController", "DurableTask", "ContinuousRunReport", "Handler", "ProviderQuotaError", "LeaseLostError", "TaskCancellationRequested", "TaskDeadlineExceeded"]