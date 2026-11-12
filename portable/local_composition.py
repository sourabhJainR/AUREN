"""Factory-free, single-user local runtime composition.

The local host owns identity, assignment, Guard policy, durable approvals,
bounded budgets, plan dispatch and provider invocation. MCP only admits work
through this composition; it never calls AdaptiveRuntime.run directly.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest

from .adaptive_runtime import AdaptiveRuntime
from .adaptive_trigger import AdaptiveTrigger, AdaptiveTriggerRequest
from .agent_org_runtime import REGISTRY_PATH, authorize_agent_assignment
from .durable_approval import ApprovalPending, DurableApprovalStore
from .engineering_plan import EngineeringPlan, PlanTask
from .requirement_contract import Requirement, RequirementContractEngine
from .engineering_plan_goal_reconciler import EngineeringPlanGoalReconciler
from .engineering_plan_recovery import EngineeringPlanRecoveryBridge
from .engineering_plan_store import EngineeringPlanStore
from .organization_guard import (
    evaluate_guard_policy, guard_policy_fingerprint, load_guard_policy,
)
from .orchestration import Graph, Node, NodeKind
from .remote_model_providers import provider_pool_from_environment
from .muxin_session_adapter import LocalModelDiscovery
from .hybrid_model_router import HybridModelRouter
from .runtime_recovery import RuntimeExecutionGuard
from .side_effect_ledger import DurableSideEffectLedger


_OPERATOR_ID = "local-operator"
_LOCAL_ISSUER = "auren-local"
_HANDLER = "local_provider_task"
_VIBE_HANDLER = "local_vibe_coding_task"
_HANDLER_ACTION = "propose_changes"
_ASSIGNMENT_SCOPE = ("task-intent",)
_PROVIDER_POOLS: dict[str, Any] = {}
_PROVIDER_POOLS_LOCK = threading.RLock()
_LOCAL_COMPOSITIONS: dict[str, "LocalRuntimeComposition"] = {}
_LOCAL_COMPOSITIONS_LOCK = threading.RLock()


def _principal_digest(principal: Any) -> str:
    issuer = getattr(principal, "issuer", None)
    subject = getattr(principal, "subject", None)
    if not isinstance(issuer, str) or not issuer.strip() or not isinstance(subject, str) or not subject.strip():
        raise PermissionError("authenticated principal identity is incomplete")
    return hashlib.sha256((issuer.rstrip("/") + "\0" + subject).encode("utf-8")).hexdigest()


def _canonical_report_digest(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(report)).encode("utf-8")).hexdigest()


class LocalProviderNotConfigured(RuntimeError):
    """No explicitly configured direct API provider is available."""


class LocalProviderOutcomeUnknown(RuntimeError):
    """A provider call may have started; replay is blocked pending reconciliation."""


@dataclass(frozen=True)
class LocalTaskOutcome:
    status: str
    task_id: str
    evidence_refs: tuple[str, ...] = ()
    next_action: str = ""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _remote_policy_digest() -> str:
    """Remote inference is opt-in and restricted to explicitly public workspaces."""
    enabled = os.environ.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower()
    classification = os.environ.get("AUREN_LOCAL_DATA_CLASSIFICATION", "").strip().lower()
    if enabled != "enabled" or classification != "public":
        return ""
    return _digest("auren-local-remote-inference-v1|enabled|public")


def _local_intake_node(state: Mapping[str, Any]) -> Mapping[str, Any]:
    """Deterministic graph boundary; provider work is dispatched by the controller."""
    intent = state.get("local_task_intent")
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 20_000:
        raise ValueError("local task intent is missing or exceeds the configured limit")
    return {
        "intent_digest": _digest(intent.strip()),
        "workspace_id": str(state.get("local_workspace_id", "")),
        "admission": "host_authorization_required",
    }


def _provider_ledger_path(state_root: Path) -> Path:
    return state_root / "provider-operations.sqlite3"


class LocalProviderOperationLedger:
    """Persist one provider dispatch per stable work key; never replay ambiguity."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS provider_operations(
                    work_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('dispatching','completed','outcome_unknown')),
                    response_json TEXT NOT NULL DEFAULT '',
                    response_digest TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def begin(self, work_key: str) -> Mapping[str, Any] | None:
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db, db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,response_json,response_digest FROM provider_operations WHERE work_key=?",
                (work_key,),
            ).fetchone()
            if row is not None:
                if row[0] == "dispatching":
                    db.execute(
                        "UPDATE provider_operations SET status='outcome_unknown',updated_at=? WHERE work_key=? AND status='dispatching'",
                        (time.time(), work_key),
                    )
                    db.commit()
                    raise LocalProviderOutcomeUnknown(
                        "provider operation was interrupted after dispatch; outcome is unknown and automatic replay is blocked"
                    )
                db.commit()
                if row[0] == "completed":
                    response = json.loads(row[1])
                    if not isinstance(response, Mapping) or _digest(_canonical(response)) != row[2]:
                        raise LocalProviderOutcomeUnknown("stored provider response failed digest verification")
                    return dict(response)
                raise LocalProviderOutcomeUnknown(
                    "provider operation has an unknown outcome; automatic replay is blocked"
                )
            db.execute(
                "INSERT INTO provider_operations(work_key,status,updated_at) VALUES(?,?,?)",
                (work_key, "dispatching", time.time()),
            )
            db.commit()
        return None

    def complete(self, work_key: str, response: Mapping[str, Any]) -> None:
        encoded = _canonical(dict(response))
        digest = _digest(encoded)
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db, db:
            db.execute("PRAGMA busy_timeout=10000")
            cursor = db.execute(
                "UPDATE provider_operations SET status='completed',response_json=?,response_digest=?,updated_at=? "
                "WHERE work_key=? AND status='dispatching'",
                (encoded, digest, time.time(), work_key),
            )
            if cursor.rowcount != 1:
                raise LocalProviderOutcomeUnknown("provider operation state changed before its result was committed")

    def mark_unknown(self, work_key: str) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "UPDATE provider_operations SET status='outcome_unknown',updated_at=? "
                "WHERE work_key=? AND status='dispatching'",
                (time.time(), work_key),
            )

    def response(self, work_key: str) -> Mapping[str, Any] | None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            row = db.execute(
                "SELECT status,response_json,response_digest FROM provider_operations WHERE work_key=?",
                (work_key,),
            ).fetchone()
        if row is None or row[0] != "completed":
            return None
        value = json.loads(row[1])
        if not isinstance(value, Mapping) or _digest(_canonical(value)) != row[2]:
            raise LocalProviderOutcomeUnknown("stored provider response failed digest verification")
        return dict(value)


class _BoundLocalModelDiscovery:
    """Re-discover one admission-bound local model without selecting a substitute."""

    def __init__(self, model: str) -> None:
        from .muxin_session_adapter import LocalModelDiscovery
        self.delegate = LocalModelDiscovery()
        self.base_url = self.delegate.base_url
        self.model = model

    def discover(self) -> Mapping[str, Any]:
        report = self.delegate.discover()
        models = report.get("models", []) if isinstance(report, Mapping) else []
        selected = [
            item for item in models
            if isinstance(item, Mapping) and item.get("name") == self.model
        ]
        return {**dict(report), "models": selected, "available": bool(selected)}


def run_local_provider_task(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run one admission-bound local or remote model call with durable replay fencing."""
    if not isinstance(payload, Mapping):
        raise TypeError("local provider payload must be a mapping")
    task = payload.get("task")
    work_key = payload.get("work_key")
    raw_state_root = payload.get("_auren_local_state_root")
    binding = payload.get("_auren_local_provider")
    if (
        not isinstance(task, str) or not task.strip() or len(task) > 20_000
        or not isinstance(work_key, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{16,200}", work_key)
        or not isinstance(raw_state_root, str) or not Path(raw_state_root).is_absolute()
        or not isinstance(binding, Mapping)
    ):
        raise ValueError("host-bound local provider task fields are invalid")
    supplied_state_root = Path(raw_state_root).expanduser()
    if supplied_state_root.is_symlink():
        raise PermissionError("local provider state root must not be a symlink")
    state_root = supplied_state_root.resolve()
    if not state_root.is_dir():
        raise PermissionError("local provider state root is unavailable or unsafe")

    kind = binding.get("kind", "remote")
    name = binding.get("name")
    model = binding.get("model")
    if (
        kind not in {"local", "remote"}
        or not isinstance(name, str) or not name
        or not isinstance(model, str) or not model
    ):
        raise ValueError("admission-bound model identity is invalid")
    remote_policy_digest = _remote_policy_digest()
    pool = None
    if kind == "remote":
        if (
            not remote_policy_digest
            or payload.get("_auren_local_remote_policy_digest") != remote_policy_digest
        ):
            raise LocalProviderNotConfigured(
                "remote inference is not enabled for an explicitly public local workspace"
            )
        with _PROVIDER_POOLS_LOCK:
            pool = _PROVIDER_POOLS.get(str(state_root))
        if pool is None:
            pool = provider_pool_from_environment()
        if pool is None:
            raise LocalProviderNotConfigured("the admission-bound remote provider is no longer configured")
        providers = getattr(pool, "providers", ())
        selected = next((
            item for item in providers
            if getattr(getattr(item, "spec", None), "name", None) == name
            and getattr(item, "model", None) == model
        ), None)
        if selected is None:
            raise LocalProviderNotConfigured(
                "the admission-bound remote provider or model changed; submit a new task"
            )
    elif name != "ollama-compatible":
        raise LocalProviderNotConfigured("local model provider identity is not supported")

    ledger = LocalProviderOperationLedger(_provider_ledger_path(state_root))
    cached = ledger.begin(work_key)
    if cached is not None:
        return cached
    prompt = (
        "You are a model provider inside AUREN. You have no repository tools and cannot change files. "
        "Do not claim to have inspected a repository or run commands. Provide analysis or a proposed plan only.\n\n"
        "User task:\n" + task.strip()
    )
    try:
        from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
        discovery = _BoundLocalModelDiscovery(model) if kind == "local" else None
        router = HybridModelRouter(discovery=discovery, remote_executor=pool)
        request = TaskRouteRequest(
            task=prompt,
            capability="general",
            privacy="private" if kind == "local" else "public",
            max_attempts=1,
            timeout_seconds=120.0,
            provider_order=(name,) if kind == "remote" else None,
        )
        routed = router.route_supervised(request, execution_payload=payload)
        content = routed.result
        if not routed.success or not isinstance(content, str) or not content.strip():
            raise LocalProviderNotConfigured(
                routed.error or "selected model route returned no usable text"
            )
        if kind == "local" and (routed.provider != "local" or routed.model != model):
            raise LocalProviderNotConfigured(
                "local model route did not use the model bound at task admission"
            )
        if kind == "remote" and (routed.provider != name or routed.model != model):
            raise LocalProviderNotConfigured(
                "remote model route did not use the provider bound at task admission"
            )
        if len(content) > 100_000:
            raise ValueError("local model response exceeded the local evidence size limit")
        computed_response_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        result = {
            "status": "completed",
            "provider": name,
            "model": model,
            "content": content,
            "usage": dict(routed.provider_metadata.get("usage", {}))
            if isinstance(routed.provider_metadata, Mapping)
            and isinstance(routed.provider_metadata.get("usage"), Mapping) else {},
            "latency_ms": routed.latency_ms,
            "response_digest": computed_response_digest,
        }
        ledger.complete(work_key, result)
        return result
    except BaseException:
        ledger.mark_unknown(work_key)
        raise

class LocalOperatorAuthority:
    """Persistent host-owned operator identity and role authorization."""

    def __init__(self, path: Path, project_root: Path, policy_path: Path | None = None) -> None:
        self.path = Path(path)
        self.project_root = Path(project_root).resolve()
        self.policy_path = Path(policy_path) if policy_path is not None else self.project_root / ".auren" / "guard_policy.json"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.principal_digest = _digest(_LOCAL_ISSUER + "\0" + _OPERATOR_ID)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS local_operator(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    operator_id TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    principal_digest TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK(active IN (0,1)),
                    created_at REAL NOT NULL
                )
            """)
            row = db.execute("SELECT operator_id,issuer,principal_digest,project_root,active FROM local_operator WHERE singleton=1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO local_operator VALUES(1,?,?,?,?,1,?)",
                    (_OPERATOR_ID, _LOCAL_ISSUER, self.principal_digest, str(self.project_root), time.time()),
                )
            elif row != (_OPERATOR_ID, _LOCAL_ISSUER, self.principal_digest, str(self.project_root), 1):
                raise PermissionError("persisted local operator identity does not match this workspace")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def verify_digest(self, principal_digest: str) -> bool:
        if principal_digest != self.principal_digest:
            return False
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            row = db.execute(
                "SELECT operator_id,issuer,principal_digest,project_root,active "
                "FROM local_operator WHERE singleton=1"
            ).fetchone()
        return bool(
            row == (
                _OPERATOR_ID, _LOCAL_ISSUER, self.principal_digest,
                str(self.project_root), 1,
            )
        )

    def verify_principal(self, principal: Any) -> bool:
        return bool(
            getattr(principal, "subject", None) == _OPERATOR_ID
            and getattr(principal, "issuer", None) == _LOCAL_ISSUER
            and self.verify_digest(self.principal_digest)
        )

    def authorize_assignment(self, request: Mapping[str, Any], *, policy_path: Path) -> bool:
        if (
            not isinstance(request, Mapping)
            or request.get("principal_id") != _OPERATOR_ID
            or request.get("role_id") != "BE-01"
            or request.get("manager_id") != "EM-ENG-01"
            or request.get("action_class") != _HANDLER_ACTION
            or tuple(request.get("scope", ())) != _ASSIGNMENT_SCOPE
            or not self.verify_digest(self.principal_digest)
        ):
            return False
        policy = load_guard_policy(policy_path)
        decision = evaluate_guard_policy(
            policy,
            {
                "action": "task.assign", "tool": "local-agent-assignment",
                "resource": "task-intent", "risk": "standard",
            },
            mandatory_authorized=True,
        )
        return decision.allowed is True

    def authorize_approver(self, approver_id: str, required_role: str, scope: str, roles: tuple[str, ...]) -> bool:
        configured_remote_approver_digests = {
            item.strip().lower()
            for item in os.environ.get("AUREN_MCP_APPROVER_SUBJECT_DIGESTS", "").split(",")
            if re.fullmatch(r"[0-9a-fA-F]{64}", item.strip())
        }
        return bool(
            (
                approver_id in {self.principal_digest, _OPERATOR_ID}
                or approver_id in configured_remote_approver_digests
            )
            and required_role == "engineering_manager"
            and scope in {"repo:write", "project:write"}
            and "engineering_manager" in roles
            and self.verify_digest(self.principal_digest)
        )

    def approval_context(self, request: Any) -> Mapping[str, str]:
        policy = load_guard_policy(self.policy_path)
        evidence = _canonical(list(getattr(request, "evidence_refs", ())))
        return {
            "artifact_digest": _digest(str(getattr(request, "target", ""))),
            "evidence_digest": _digest(evidence),
            "policy_digest": guard_policy_fingerprint(policy),
        }


class LocalBudgetResolver:
    """Persist one immutable supervisor budget checkpoint per plan task."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS local_budgets(
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    handler TEXT NOT NULL,
                    budget_id TEXT NOT NULL UNIQUE,
                    snapshot_json TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    PRIMARY KEY(run_id,task_id,handler)
                )
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def resolve(self, run_id: str, task_id: str, handler: str) -> Mapping[str, Any]:
        if handler == _VIBE_HANDLER:
            policy_digest = _digest("auren-local-vibe-budget-v1|max_steps=64|max_attempts=1|deadline=3600")
            max_steps, max_attempts, deadline_seconds = 64, 1, 3600
        else:
            policy_digest = _digest("auren-local-budget-v1|max_steps=8|max_attempts=4|deadline=300")
            max_steps, max_attempts, deadline_seconds = 8, 4, 300
        budget_id = "local:" + _digest(run_id + "\0" + task_id + "\0" + handler)[:40]
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db, db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT budget_id,snapshot_json,policy_digest FROM local_budgets "
                "WHERE run_id=? AND task_id=? AND handler=?",
                (run_id, task_id, handler),
            ).fetchone()
            if row is None:
                guard = RuntimeExecutionGuard(
                    max_steps=max_steps, max_attempts=max_attempts,
                    deadline_seconds=deadline_seconds,
                )
                snapshot = guard.snapshot()
                db.execute(
                    "INSERT INTO local_budgets VALUES(?,?,?,?,?,?)",
                    (run_id, task_id, handler, budget_id, _canonical(snapshot), policy_digest),
                )
            else:
                if row[0] != budget_id or row[2] != policy_digest:
                    db.rollback()
                    raise PermissionError("persisted local budget identity or policy changed")
                snapshot = json.loads(row[1])
                guard = RuntimeExecutionGuard.resume(snapshot)
            db.commit()
        return {
            "budget_id": budget_id,
            "guard": guard,
            "checkpoint_digest": snapshot["digest"],
            "policy_digest": policy_digest,
        }


class LocalRuntimeComposition:
    """One built-in runtime shared by local MCP, the canonical controller and stores."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        state_root: str | Path,
        provider_pool: Any | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise ValueError("local project root must be an existing directory")
        self.state_root = Path(state_root).expanduser()
        self.state_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_root.exists() and self.state_root.is_symlink():
            raise PermissionError("local runtime state root must not be a symlink")
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_root = self.state_root.resolve()
        if not self.state_root.is_dir():
            raise PermissionError("local runtime state root is not a directory")
        try:
            self.state_root.chmod(0o700)
        except OSError:
            pass

        self.local_model_discovery = LocalModelDiscovery()
        self.provider_pool = (
            provider_pool if provider_pool is not None
            else provider_pool_from_environment()
        )
        if self.provider_pool is not None:
            with _PROVIDER_POOLS_LOCK:
                _PROVIDER_POOLS[str(self.state_root)] = self.provider_pool

        from .vibe_coding_host import load_host_config
        self.vibe_host_config_path = Path.home().expanduser() / ".auren" / "vibe-coding" / "host.json"
        self.vibe_host_config = None
        self.vibe_host_config_digest = ""
        self.vibe_host_status: dict[str, Any] = {"status": "not_configured"}
        try:
            config = load_host_config(self.vibe_host_config_path)
            provider_command = "codex" if config.provider == "codex" else None
            config_digest = hashlib.sha256(self.vibe_host_config_path.read_bytes()).hexdigest()
            if config.allow_workspace_writes is not True:
                self.vibe_host_status = {"status": "blocked", "reason": "isolated_worktree_writes_not_enabled"}
            elif provider_command is None:
                self.vibe_host_status = {"status": "blocked", "reason": "unsupported_provider_cli"}
            elif shutil.which(provider_command) is None:
                self.vibe_host_status = {"status": "provider_missing", "provider": config.provider}
            else:
                self.vibe_host_config = config
                self.vibe_host_config_digest = config_digest
                self.vibe_host_status = {
                    "status": "ready", "provider": config.provider,
                    "config_digest": config_digest,
                }
        except FileNotFoundError:
            self.vibe_host_status = {"status": "not_configured"}
        except Exception as exc:
            self.vibe_host_status = {
                "status": "blocked", "reason": type(exc).__name__,
            }

        graph = Graph([
            Node(
                name="local_task_intake",
                kind=NodeKind.DETERMINISTIC,
                run=_local_intake_node,
                max_attempts=1,
                evaluator=lambda result: isinstance(result, Mapping) and bool(result.get("intent_digest")),
                risk="low",
                inputs=("local_task_intent",),
                outputs=("intent_digest",),
            ),
        ])
        self.runtime = AdaptiveRuntime(
            graph,
            state_root=self.state_root / "adaptive-runtime",
            max_total_attempts=8,
        )
        self.controller = self.runtime.execution_controller
        self.operator = LocalOperatorAuthority(
            self.state_root / "operator.sqlite3",
            self.project_root,
            self.controller.root / ".auren" / "guard_policy.json",
        )
        self.controller.register_handler(
            _HANDLER,
            "portable.local_composition:run_local_provider_task",
            execution_class="read_only",
            guard_action="provider.invoke",
            agent_action_class=_HANDLER_ACTION,
        )
        self.controller.register_handler(
            _VIBE_HANDLER,
            "portable.local_composition:run_local_vibe_coding_task",
            execution_class="approval_gated",
            approval_gate_policy={
                "approval_store": "DurableApprovalStore",
                "gateway": "ActionGovernanceGateway",
                "durable_side_effect_ledger": True,
            },
            guard_action="provider.invoke",
            agent_action_class=_HANDLER_ACTION,
        )
        self.plan_store = EngineeringPlanStore(self.state_root / "engineering-plans.sqlite3")
        self.recovery_bridge = EngineeringPlanRecoveryBridge(
            store=self.plan_store, ledger=self.runtime.recovery_ledger,
        )
        self.approval_store = DurableApprovalStore(
            self.controller,
            approver_authorizer=self.operator.authorize_approver,
            context_provider=self.operator.approval_context,
            wake_callback=lambda task_id, approval_id: self.controller.resume_approved_task(
                task_id, approval_id, approval_store=self.approval_store,
            ),
        )
        self.budgets = LocalBudgetResolver(self.state_root / "budgets.sqlite3")

        def assignment_resolver(context: Mapping[str, Any]):
            task = getattr(context.get("packet"), "task", None)
            payload = context.get("payload")
            if not isinstance(task, Mapping) or not isinstance(payload, Mapping):
                raise PermissionError("local assignment requires the canonical plan task and payload")
            role_id = task.get("owner_role_id")
            manager_id = task.get("manager_role_id")
            if role_id != "BE-01" or manager_id != "EM-ENG-01":
                raise PermissionError("local task role hierarchy does not match the host registry")
            return authorize_agent_assignment(
                role_id=role_id,
                manager_id=manager_id,
                action_class=_HANDLER_ACTION,
                scope=_ASSIGNMENT_SCOPE,
                authorizer=lambda request: self.operator.authorize_assignment(
                    request, policy_path=self.controller.root / ".auren" / "guard_policy.json",
                ),
                registry_path=REGISTRY_PATH,
                policy_path=self.controller.root / ".auren" / "guard_policy.json",
                work_key=str(context.get("dispatch_key", "")),
                task_payload=payload,
                deadline_seconds=context.get("deadline_seconds"),
                principal_id=_OPERATOR_ID,
            )

        self.dispatch_root = self.runtime.configure_trusted_plan_dispatch(
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=self.approval_store,
            budget_resolver=self.budgets.resolve,
            approval_resolver=lambda _run_id, _task_id, _handler: None,
            assignment_resolver=assignment_resolver,
        )
        self._supervisor_lock = threading.Lock()
        self.supervisor = self.runtime.create_continuous_supervisor(
            operator_authorizer=lambda principal_id: (
                principal_id
                if principal_id == _OPERATOR_ID
                and self.operator.verify_digest(self.operator.principal_digest)
                else None
            ),
        )
        self.trigger = AdaptiveTrigger(
            self.runtime.trigger_runtime,
            runner=self.run_trigger,
        )
        if self.vibe_host_status.get("status") == "ready":
            current_lease = getattr(self.runtime.trigger_runtime, "claim_lease_seconds", 120)
            self.runtime.trigger_runtime.claim_lease_seconds = max(int(current_lease), 3700)
        self._closed = False
        with _LOCAL_COMPOSITIONS_LOCK:
            _LOCAL_COMPOSITIONS[str(self.state_root)] = self

    @property
    def provider_ready(self) -> bool:
        if self._local_model_candidates():
            return True
        return self.provider_pool is not None and bool(_remote_policy_digest())

    def _local_model_candidates(self) -> list[str]:
        report = self.local_model_discovery.discover()
        return HybridModelRouter._ranked_candidates(
            report.get("models") if isinstance(report, Mapping) else None,
            "general",
        )

    def _remote_principal_authorized(self, principal: Any) -> bool:
        if os.environ.get("AUREN_MCP_REMOTE_EXECUTION_ENABLED") != "1":
            return False
        issuer = getattr(principal, "issuer", "")
        subject = getattr(principal, "subject", "")
        roles = tuple(getattr(principal, "roles", ()))
        projects = tuple(getattr(principal, "projects", ()))
        allows = getattr(principal, "allows", None)
        if (
            not isinstance(issuer, str) or not issuer.startswith("https://")
            or not isinstance(subject, str) or not subject.strip()
            or "engineering_manager" not in roles
            or not callable(allows) or not allows("auren:tasks:write")
            or not projects
        ):
            return False
        for project in projects:
            try:
                candidate = Path(project).expanduser().resolve(strict=True)
                if candidate.is_dir() and os.path.samefile(candidate, self.project_root):
                    return True
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
        return False

    def admit_task(
        self, principal: Any, task: str, context: Mapping[str, Any], event_id: str,
    ) -> Mapping[str, Any]:
        local_principal = self.operator.verify_principal(principal)
        remote_principal = self._remote_principal_authorized(principal)
        if not (local_principal or remote_principal):
            raise PermissionError(
                "task admission requires the local operator or an explicitly authorized remote project principal"
            )
        principal_digest = _principal_digest(principal)
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task text is required")
        policy = load_guard_policy(self.controller.root / ".auren" / "guard_policy.json")
        decision = evaluate_guard_policy(
            policy,
            {
                "action": "task.submit", "tool": "local-mcp",
                "resource": "task-intent", "risk": "standard",
            },
            mandatory_authorized=True,
        )
        if decision.allowed is not True:
            raise PermissionError("current Guard policy denied task admission: " + decision.reason)

        # Retries retain the exact principal, source, provider and Vibe host binding.
        existing = self.runtime.trigger_runtime.get_event(event_id)
        if existing is not None:
            if existing.payload.get("task") != task.strip():
                raise PermissionError("idempotency key is already bound to different task content")
            existing_context = existing.payload.get("context", {})
            if (
                not isinstance(existing_context, Mapping)
                or existing_context.get("_auren_local_subject_digest") != principal_digest
                or not isinstance(existing_context.get("_auren_local_source_revision"), str)
                or not isinstance(existing_context.get("_auren_local_provider"), Mapping)
                or not isinstance(existing_context.get("_auren_local_policy_digest"), str)
                or not isinstance(existing_context.get("_auren_local_remote_policy_digest"), str)
                or not isinstance(existing_context.get("_auren_local_admitted_at"), (int, float))
            ):
                raise PermissionError("existing task is missing its immutable host admission binding")
            return {
                "_auren_local_subject_digest": existing_context["_auren_local_subject_digest"],
                "_auren_local_source_revision": existing_context["_auren_local_source_revision"],
                "_auren_local_provider": dict(existing_context["_auren_local_provider"]),
                "_auren_local_policy_digest": existing_context["_auren_local_policy_digest"],
                "_auren_local_remote_policy_digest": existing_context["_auren_local_remote_policy_digest"],
                "_auren_local_admitted_at": existing_context["_auren_local_admitted_at"],
                "_auren_local_separation_of_duties": existing_context.get("_auren_local_separation_of_duties", False),
                "_auren_local_vibe_config_path": existing_context.get("_auren_local_vibe_config_path", ""),
                "_auren_local_vibe_config_digest": existing_context.get("_auren_local_vibe_config_digest", ""),
            }

        remote_policy_digest = _remote_policy_digest()
        vibe_enabled = self.vibe_host_config is not None and self.vibe_host_status.get("status") == "ready"
        if remote_principal and not vibe_enabled:
            raise LocalProviderNotConfigured(
                "remote task execution requires a configured Vibe Coding host and available coding-agent CLI"
            )
        if vibe_enabled:
            provider_binding = {
                "kind": "vibe", "name": self.vibe_host_config.provider, "model": "cli",
            }
            bound_remote_policy_digest = ""
        else:
            local_models = self._local_model_candidates()
            if local_models:
                provider_binding = {
                    "kind": "local", "name": "ollama-compatible", "model": local_models[0],
                }
                bound_remote_policy_digest = ""
            else:
                if self.provider_pool is None:
                    raise LocalProviderNotConfigured(
                        "no provider is configured and no capable local model is available"
                    )
                if not remote_policy_digest:
                    raise LocalProviderNotConfigured(
                        "no capable local model is available; remote inference is disabled unless "
                        "AUREN_LOCAL_REMOTE_INFERENCE=enabled and AUREN_LOCAL_DATA_CLASSIFICATION=public"
                    )
                providers = getattr(self.provider_pool, "providers", ())
                if not providers:
                    raise LocalProviderNotConfigured("configured provider pool has no reviewed provider adapter")
                selected = providers[0]
                name = getattr(getattr(selected, "spec", None), "name", None)
                model = getattr(selected, "model", None)
                if not isinstance(name, str) or not name or not isinstance(model, str) or not model:
                    raise LocalProviderNotConfigured("configured provider identity or model is incomplete")
                provider_binding = {"kind": "remote", "name": name, "model": model}
                bound_remote_policy_digest = remote_policy_digest
        source_revision = self._source_revision()
        admitted_at = time.time()
        vibe_config_path = str(self.vibe_host_config_path) if vibe_enabled else ""
        vibe_config_digest = self.vibe_host_config_digest if vibe_enabled else ""
        policy_material = guard_policy_fingerprint(policy) + "|" + bound_remote_policy_digest
        if vibe_enabled:
            policy_material += "|vibe:" + vibe_config_digest
        return {
            "_auren_local_subject_digest": principal_digest,
            "_auren_local_source_revision": source_revision,
            "_auren_local_provider": provider_binding,
            "_auren_local_policy_digest": _digest(policy_material),
            "_auren_local_remote_policy_digest": bound_remote_policy_digest,
            "_auren_local_admitted_at": admitted_at,
            "_auren_local_separation_of_duties": bool(remote_principal),
            "_auren_local_vibe_config_path": vibe_config_path,
            "_auren_local_vibe_config_digest": vibe_config_digest,
        }

    def _source_revision(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.project_root), "rev-parse", "--verify", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("local engineering tasks require a Git repository with a committed HEAD") from exc
        revision = completed.stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise RuntimeError("Git returned an invalid source revision")
        return revision

    def _plan_for(
        self, trigger_id: str, task: str, source_revision: str, policy_digest: str,
        provider_binding: Mapping[str, Any],
    ) -> EngineeringPlan:
        if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
            raise ValueError("local task is missing its host-bound Git source revision")
        vibe_execution = provider_binding.get("kind") == "vibe"
        acceptance = (
            (
                "The existing 18-stage Vibe Coding host reaches an accepted terminal run.",
                "The host report is digest-verified and bound to the admitted source revision.",
                "All repository changes remain in the isolated worktree; no merge, publish or production deployment occurs.",
                "The durable approval and side-effect receipts are complete.",
            )
            if vibe_execution else
            (
                "The admission-bound local or remote model returns a non-empty response.",
                "The response digest is durably recorded and replay-safe.",
            )
        )
        contract = RequirementContractEngine().build(
            task.strip(),
            (Requirement(
                requirement_id="R1", statement=task.strip(),
                acceptance_criteria=acceptance,
            ),),
        )
        issues = RequirementContractEngine().validate(contract)
        if issues:
            raise ValueError("local requirement contract failed admission: " + "; ".join(issues))
        return EngineeringPlan(
            run_id="local-" + trigger_id,
            goal=task.strip(),
            requirements=tuple(item.statement for item in contract.requirements),
            requirement_contract=contract.to_dict(),
            tasks=(PlanTask(
                task_id="execute",
                title=task.strip(),
                acceptance_criteria=acceptance,
                rollback_plan=(
                    "Discard or explicitly roll back the isolated Vibe Coding worktree; never alter the source checkout."
                    if vibe_execution else
                    "This provider task is read-only and must not modify the repository."
                ),
                risk="high" if vibe_execution else "low",
                owner_role_id="BE-01",
                manager_role_id="EM-ENG-01",
            ),),
            source_revision=source_revision,
            policy_digest=policy_digest,
            budget={
                "attempts": 1, "dispatches": 1,
                "tokens": 65536 if vibe_execution else 4096,
                "wall_seconds": 3600 if vibe_execution else 300,
            },
        )

    def _goal_reconciler_for(
        self,
        plan: EngineeringPlan,
        provider_binding: Mapping[str, Any],
        policy_digest: str,
        remote_policy_digest: str,
        principal_digest: str,
        admitted_at: float,
        separation_of_duties: bool,
    ) -> EngineeringPlanGoalReconciler:
        holder: dict[str, EngineeringPlanGoalReconciler] = {}
        vibe_execution = provider_binding.get("kind") == "vibe"

        def payload_resolver(_plan: EngineeringPlan, task: PlanTask) -> Mapping[str, Any]:
            if vibe_execution:
                return {
                    "task": task.title,
                    "resource": "task-intent",
                    "work_key": holder["reconciler"].work_key(task, 0),
                    "run_id": _plan.run_id,
                    "workflow_id": "auren-" + _plan.run_id.removeprefix("local-"),
                    "project_root": str(self.project_root),
                    "state_root": str(self.state_root),
                    "host_config_path": str(self.vibe_host_config_path),
                    "host_config_digest": self.vibe_host_config_digest,
                    "source_revision": _plan.source_revision,
                    "policy_digest": policy_digest,
                    "requester_id": principal_digest,
                    "admitted_at": admitted_at,
                    "separation_of_duties": separation_of_duties,
                    "auren_approval_aware": True,
                    "_auren_local_state_root": str(self.state_root),
                    "_auren_local_provider": dict(provider_binding),
                    "_auren_local_policy_digest": policy_digest,
                    "_auren_local_remote_policy_digest": remote_policy_digest,
                }
            return {
                "task": task.title,
                "resource": "task-intent",
                "_auren_local_state_root": str(self.state_root),
                "_auren_local_provider": dict(provider_binding),
                "_auren_local_policy_digest": policy_digest,
                "_auren_local_remote_policy_digest": remote_policy_digest,
            }

        def budget_resolver(
            current_plan: EngineeringPlan, _run: Any, _task: PlanTask,
        ) -> Mapping[str, int]:
            return {
                "attempts": min(1, int(current_plan.budget.get("attempts", 1))),
                "steps": 64 if vibe_execution else 1,
                "tool_calls": 64 if vibe_execution else 1,
                "tokens": min(
                    65536 if vibe_execution else 4096,
                    int(current_plan.budget.get("tokens", 65536 if vibe_execution else 4096)),
                ),
            }

        def completion_verifier(
            current_plan: EngineeringPlan, task: PlanTask, durable: Any,
        ) -> Mapping[str, Any]:
            if getattr(durable, "state", None) != "completed":
                return {"verified": False, "reason": "canonical controller task is not complete"}
            _stored_plan, current_run = self.recovery_bridge.load_verified(current_plan.run_id)
            progress = next(
                item for item in current_run.tasks if item.task_id == task.task_id
            )
            reconciler = holder["reconciler"]
            work_key = reconciler.work_key(task, progress.replan_generation)
            if vibe_execution:
                workflow_id = "auren-" + current_plan.run_id.removeprefix("local-")
                report_path = self.state_root / "vibe-coding" / "reports" / f"{workflow_id}.json"
                if report_path.is_symlink() or not report_path.is_file():
                    return {"verified": False, "reason": "Vibe Coding report is missing or unsafe"}
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    recorded_digest = report.pop("digest")
                    stage_statuses = report.get("stage_statuses")
                    workspace_root = Path(report.get("workspace_root", "")).resolve()
                    worktree_root = (self.state_root / "vibe-coding" / "worktrees").resolve()
                    if (
                        not isinstance(stage_statuses, Mapping)
                        or len(stage_statuses) != 18
                        or report.get("run_status") != "accepted"
                        or report.get("base_revision") != current_plan.source_revision
                        or report.get("workspace_head") != current_plan.source_revision
                        or report.get("merge_or_publish_performed") is not False
                        or report.get("production_deployment_performed") is not False
                        or workspace_root == worktree_root
                        or worktree_root not in workspace_root.parents
                        or _canonical_report_digest(report) != recorded_digest
                    ):
                        return {"verified": False, "reason": "Vibe Coding report failed stage, isolation, source or digest verification"}
                    if not self.approval_store.verify_audit_chain():
                        return {"verified": False, "reason": "durable approval audit chain is invalid"}
                    completed_approval = None
                    for event in self.approval_store.events():
                        if event.get("event_type") != "operation_completed":
                            continue
                        approval_id = event.get("approval_id")
                        if not isinstance(approval_id, str) or not approval_id:
                            continue
                        approval_record = self.approval_store.get(approval_id)
                        if (
                            approval_record.operation_id == work_key
                            and approval_record.status == "completed"
                            and re.fullmatch(r"[0-9a-f]{64}", approval_record.receipt_digest)
                        ):
                            completed_approval = approval_record
                            break
                    if completed_approval is None:
                        return {"verified": False, "reason": "Vibe Coding durable approval receipt is missing"}
                    side_effect = DurableSideEffectLedger(
                        self.state_root / "vibe-coding" / "canonical-side-effects.sqlite3"
                    ).get(work_key)
                    side_effect_receipt = side_effect.get("receipt") if isinstance(side_effect, Mapping) else None
                    if (
                        not isinstance(side_effect, Mapping)
                        or side_effect.get("state") != "completed"
                        or not isinstance(side_effect_receipt, Mapping)
                        or not re.fullmatch(r"[0-9a-f]{64}", str(side_effect_receipt.get("result_digest", "")))
                        or side_effect_receipt.get("request_fingerprint") != completed_approval.request_fingerprint
                        or side_effect_receipt.get("result_digest") != completed_approval.receipt_digest
                    ):
                        return {"verified": False, "reason": "Vibe Coding durable side-effect receipt is missing or inconsistent"}
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    return {"verified": False, "reason": "Vibe Coding report or approval receipt could not be verified"}
                return {
                    "verified": True,
                    "evidence_refs": (
                        "sha256:" + recorded_digest,
                        "sha256:" + completed_approval.receipt_digest,
                    ),
                }

            response = LocalProviderOperationLedger(
                _provider_ledger_path(self.state_root)
            ).response(work_key)
            if (
                not isinstance(response, Mapping)
                or not isinstance(response.get("content"), str)
                or not response.get("content", "").strip()
                or response.get("provider") != provider_binding.get("name")
                or response.get("model") != provider_binding.get("model")
            ):
                return {"verified": False, "reason": "durable provider response or selected provider identity is unavailable"}
            response_digest = hashlib.sha256(response["content"].encode("utf-8")).hexdigest()
            if response.get("response_digest") != response_digest:
                return {"verified": False, "reason": "provider response digest failed content verification"}
            return {"verified": True, "evidence_refs": ("sha256:" + response_digest,)}

        reconciler = EngineeringPlanGoalReconciler(
            run_id=plan.run_id,
            controller=self.controller,
            recovery_bridge=self.recovery_bridge,
            trusted_dispatch_root=self.dispatch_root,
            handler_resolver=lambda _plan, _task: (
                _VIBE_HANDLER if vibe_execution else _HANDLER
            ),
            payload_resolver=payload_resolver,
            budget_resolver=budget_resolver,
            completion_verifier=completion_verifier,
            policy_constraints=(
                "The canonical ExecutionController, current Guard policy, durable approval store and action gateway remain authoritative.",
                "Vibe Coding changes are confined to an isolated Git worktree; the source checkout is never modified.",
                "Merge, publish, release and production deployment are not authorized by this task.",
                "A durable approval and side-effect receipt are required before the Vibe Coding host starts.",
            ) if vibe_execution else (
                "Provider inference is proposal-only and has no repository write tools.",
                "No merge, release, deployment, publication or other external action is authorized.",
                "The canonical ExecutionController and current Guard policy remain authoritative.",
            ),
        )
        holder["reconciler"] = reconciler
        self.supervisor.goal_reconcilers[reconciler.goal_key] = reconciler
        return reconciler

    def run_trigger(self, request: AdaptiveTriggerRequest, trigger_id: str) -> LocalTaskOutcome:
        principal_digest = request.context.get("_auren_local_subject_digest")
        if (
            not isinstance(principal_digest, str)
            or request.context.get("_auren_mcp_subject") != principal_digest
            or not self.operator.verify_digest(self.operator.principal_digest)
        ):
            raise PermissionError("persisted MCP event is not bound to an admitted principal and host-owned operator")
        source_revision = request.context.get("_auren_local_source_revision")
        provider_binding = request.context.get("_auren_local_provider")
        policy_digest = request.context.get("_auren_local_policy_digest")
        remote_policy_digest = _remote_policy_digest()
        current_policy = load_guard_policy(self.controller.root / ".auren" / "guard_policy.json")
        provider_kind = provider_binding.get("kind", "remote") if isinstance(provider_binding, Mapping) else None
        bound_remote_policy_digest = remote_policy_digest if provider_kind == "remote" else ""
        vibe_config_digest = request.context.get("_auren_local_vibe_config_digest", "")
        policy_material = guard_policy_fingerprint(current_policy) + "|" + bound_remote_policy_digest
        if provider_kind == "vibe":
            if (
                self.vibe_host_config is None
                or request.context.get("_auren_local_vibe_config_path") != str(self.vibe_host_config_path)
                or vibe_config_digest != self.vibe_host_config_digest
            ):
                raise PermissionError("admitted Vibe Coding host configuration changed or is unavailable")
            policy_material += "|vibe:" + vibe_config_digest
        if (
            not isinstance(source_revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
            or not isinstance(provider_binding, Mapping)
            or provider_kind not in {"local", "remote", "vibe"}
            or not isinstance(policy_digest, str)
            or request.context.get("_auren_local_remote_policy_digest") != bound_remote_policy_digest
            or (provider_kind == "remote" and not remote_policy_digest)
            or policy_digest != _digest(policy_material)
        ):
            raise PermissionError("persisted MCP event lacks current host-bound source, provider or model-policy evidence")

        admitted_at = request.context.get("_auren_local_admitted_at")
        if not isinstance(admitted_at, (int, float)) or isinstance(admitted_at, bool):
            raise PermissionError("persisted MCP event is missing its immutable admission timestamp")
        plan = self._plan_for(trigger_id, request.task, source_revision, policy_digest, provider_binding)
        try:
            stored_plan, _stored_run = self.plan_store.load(plan.run_id)
            if stored_plan.digest != plan.digest:
                raise PermissionError("stable local task ID was reused for different plan content or source revision")
        except KeyError:
            pass
        self.recovery_bridge.start(plan)
        reconciler = self._goal_reconciler_for(
            plan, provider_binding, policy_digest, remote_policy_digest,
            principal_digest, float(admitted_at),
            request.context.get("_auren_local_separation_of_duties") is True,
        )
        deadline = time.monotonic() + (3600.0 if provider_kind == "vibe" else 330.0)
        while time.monotonic() < deadline:
            try:
                with self._supervisor_lock:
                    self.supervisor.run(
                        poll_seconds=0.1,
                        batch_size=10,
                        max_cycles=1,
                    )
            except RuntimeError as exc:
                if "another AUREN continuous supervisor holds the live lease" not in str(exc):
                    return LocalTaskOutcome(
                        "blocked", "local-" + trigger_id, (),
                        "continuous supervisor is stopped or requires an explicit operator recovery action",
                    )

            _current_plan, current_run = self.recovery_bridge.load_verified(plan.run_id)
            progress = current_run.tasks[0]
            if progress.status == "passed":
                work_key = reconciler.work_key(plan.tasks[0], progress.replan_generation)
                durable = self.controller.task_by_work_key(work_key)
                if durable is None or durable.state != "completed":
                    raise RuntimeError("passed local goal is missing its completed canonical controller task")
                if provider_kind == "vibe":
                    expected_report = self.state_root / "vibe-coding" / "reports" / (
                        "auren-" + trigger_id + ".json"
                    )
                    if not expected_report.is_file() or expected_report.is_symlink():
                        raise RuntimeError("passed Vibe Coding goal is missing its durable host report")
                    report = json.loads(expected_report.read_text(encoding="utf-8"))
                    report_digest = report.get("digest")
                    if not isinstance(report_digest, str) or ("sha256:" + report_digest) not in progress.evidence_refs:
                        raise RuntimeError("Vibe Coding goal evidence does not match its durable host report")
                    return LocalTaskOutcome(
                        "completed", durable.id, progress.evidence_refs,
                        "vibe_coding_report_and_approval_verified",
                    )
                response = LocalProviderOperationLedger(
                    _provider_ledger_path(self.state_root)
                ).response(work_key)
                if response is None:
                    raise RuntimeError("passed local goal is missing its durable provider receipt")
                if (
                    not isinstance(response.get("content"), str)
                    or not response.get("content", "").strip()
                    or response.get("provider") != provider_binding.get("name")
                    or response.get("model") != provider_binding.get("model")
                ):
                    raise RuntimeError("completed provider receipt does not match the admitted provider identity")
                response_digest = hashlib.sha256(response["content"].encode("utf-8")).hexdigest()
                if response.get("response_digest") != response_digest:
                    raise RuntimeError("completed provider response digest failed content verification")
                expected_evidence = "sha256:" + response_digest
                if expected_evidence not in progress.evidence_refs:
                    raise RuntimeError("completed local goal evidence does not match its durable provider response")
                return LocalTaskOutcome(
                    "completed", durable.id, progress.evidence_refs,
                    "provider_response_evidence_recorded",
                )
            if progress.status in {"blocked", "failed", "cancelled"}:
                return LocalTaskOutcome(
                    "blocked", "local-" + trigger_id, progress.evidence_refs,
                    "inspect the durable blocker; explicit operator reauthorization or replanning is required",
                )
            time.sleep(0.2)
        return LocalTaskOutcome(
            "blocked", "local-" + trigger_id, (),
            "local goal remains active; inspect the supervisor and durable plan before resuming",
        )

    def close(self) -> None:
        if self._closed:
            return
        self.trigger.close(wait_for_background=True)
        with _PROVIDER_POOLS_LOCK:
            _PROVIDER_POOLS.pop(str(self.state_root), None)
        with _LOCAL_COMPOSITIONS_LOCK:
            _LOCAL_COMPOSITIONS.pop(str(self.state_root), None)
        self._closed = True


def run_local_vibe_coding_task(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run the existing Vibe Coding host behind the canonical approval and action gates."""
    if not isinstance(payload, Mapping):
        raise TypeError("Vibe Coding task payload must be a mapping")
    state_root_value = payload.get("_auren_local_state_root")
    execution_context = payload.get("_auren_execution_context")
    if (
        not isinstance(state_root_value, str) or not Path(state_root_value).is_absolute()
        or not isinstance(execution_context, Mapping)
        or not isinstance(execution_context.get("task_id"), str)
        or payload.get("auren_approval_aware") is not True
    ):
        raise PermissionError("Vibe Coding execution requires canonical approval-aware controller context")
    state_root = Path(state_root_value).resolve()
    with _LOCAL_COMPOSITIONS_LOCK:
        composition = _LOCAL_COMPOSITIONS.get(str(state_root))
    if composition is None or composition._closed:
        raise RuntimeError("canonical local runtime composition is unavailable; execution is blocked")
    if not composition.operator.verify_digest(composition.operator.principal_digest):
        raise PermissionError("host-owned operator identity failed verification")

    project_root = Path(str(payload.get("project_root", ""))).expanduser().resolve()
    if project_root != composition.project_root:
        raise PermissionError("Vibe Coding project root does not match the admitted workspace")
    source_revision = payload.get("source_revision")
    if not isinstance(source_revision, str) or composition._source_revision() != source_revision:
        raise PermissionError("source checkout revision changed after task admission")
    work_key = payload.get("work_key")
    workflow_id = payload.get("workflow_id")
    if (
        not isinstance(work_key, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{16,200}", work_key)
        or not isinstance(workflow_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", workflow_id)
    ):
        raise ValueError("Vibe Coding task identity is invalid")

    config_path = Path(str(payload.get("host_config_path", ""))).expanduser()
    if config_path != composition.vibe_host_config_path or config_path.is_symlink() or not config_path.is_file():
        raise PermissionError("Vibe Coding host configuration is missing or not host-owned")
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if (
        config_digest != payload.get("host_config_digest")
        or config_digest != composition.vibe_host_config_digest
        or composition.vibe_host_config is None
    ):
        raise PermissionError("Vibe Coding host configuration changed after task admission")
    from .vibe_coding_host import LocalVibeCodingHost, load_host_config
    config = load_host_config(config_path)
    if config.allow_workspace_writes is not True:
        raise PermissionError("Vibe Coding isolated-worktree writes are not explicitly enabled")

    task = payload.get("task")
    if not isinstance(task, str) or not task.strip() or len(task) > 20_000:
        raise ValueError("Vibe Coding task intent is missing or exceeds the configured limit")
    principal_digest = payload.get("requester_id")
    admitted_at = payload.get("admitted_at")
    if not isinstance(principal_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", principal_digest):
        raise PermissionError("Vibe Coding task lacks its authenticated requester binding")
    if not isinstance(admitted_at, (int, float)) or isinstance(admitted_at, bool):
        raise PermissionError("Vibe Coding task lacks its immutable admission timestamp")
    policy_digest = payload.get("policy_digest")
    if not isinstance(policy_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", policy_digest):
        raise PermissionError("Vibe Coding task lacks its admitted Guard policy digest")

    controller_task_id = execution_context["task_id"]
    request = ActionRequest(
        run_id=str(payload.get("run_id", "")),
        action_id=work_key,
        actor=principal_digest,
        capability="repository",
        action="edit",
        target="project:" + _digest(str(project_root))[:24],
        risk="write",
        idempotency_key=work_key,
        evidence_refs=("git:sha1:" + source_revision,),
        task_id=controller_task_id,
    )
    approval_context = composition.operator.approval_context(request)
    checkpoint = {
        "operation_id": work_key,
        "artifact_digest": approval_context["artifact_digest"],
        "evidence_digest": approval_context["evidence_digest"],
        "policy_digest": approval_context["policy_digest"],
        "request_fingerprint": request.fingerprint(),
        "source_revision": source_revision,
        "intent_digest": _digest(task.strip()),
        "host_config_digest": config_digest,
    }
    expires_at = datetime.fromtimestamp(float(admitted_at), timezone.utc) + timedelta(minutes=30)
    approval = composition.approval_store.create_request(
        request,
        operation_id=work_key,
        artifact_digest=approval_context["artifact_digest"],
        evidence_digest=approval_context["evidence_digest"],
        policy_digest=approval_context["policy_digest"],
        requester_id=principal_digest,
        required_role="engineering_manager",
        scope="repo:write",
        expires_at=expires_at,
        required_approvals=1,
        separation_of_duties=payload.get("separation_of_duties") is True,
        checkpoint=checkpoint,
    )
    report_path = composition.state_root / "vibe-coding" / "reports" / f"{workflow_id}.json"
    if approval.status == "pending":
        raise ApprovalPending(approval.approval_id, checkpoint)
    if approval.status == "completed":
        if not report_path.is_file() or report_path.is_symlink():
            raise RuntimeError("completed Vibe Coding approval has no durable host report")
    elif approval.status == "approved":
        ledger = DurableSideEffectLedger(
            composition.state_root / "vibe-coding" / "canonical-side-effects.sqlite3"
        )
        gateway = ActionGovernanceGateway(
            ActionPolicy(
                allowed_capabilities=frozenset({"repository"}),
                allowed_actions=frozenset({"edit"}),
                approval_required_actions=frozenset({"edit"}),
                external_effect_actions=frozenset({"edit"}),
                evidence_required_actions=frozenset({"edit"}),
                require_idempotency_for_external_effects=True,
                require_durable_ledger_for_external_effects=True,
            ),
            approval_verifier=composition.approval_store.verify_receipt,
            side_effect_ledger=ledger,
        )

        def execute_host() -> str:
            report = LocalVibeCodingHost(
                project_root,
                config=config,
                state_root=composition.state_root / "vibe-coding",
            ).run(workflow_id, task)
            return str(report["digest"])

        governed = composition.approval_store.execute_approved_action(
            approval.approval_id,
            request,
            gateway,
            execute_host,
            artifact_digest=approval_context["artifact_digest"],
            evidence_digest=approval_context["evidence_digest"],
            policy_digest=approval_context["policy_digest"],
        )
        if not (getattr(governed, "executed", False) or getattr(governed, "replayed", False)):
            raise PermissionError(
                "Vibe Coding host was denied by the canonical action gateway: "
                + str(getattr(getattr(governed, "verdict", None), "reason", "unknown"))
            )
    else:
        raise PermissionError("Vibe Coding task is blocked by its durable approval state: " + approval.status)

    if not report_path.is_file() or report_path.is_symlink():
        raise RuntimeError("Vibe Coding host did not produce a safe durable report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    recorded_digest = report.get("digest")
    report_without_digest = dict(report)
    report_without_digest.pop("digest", None)
    if (
        not isinstance(recorded_digest, str)
        or _canonical_report_digest(report_without_digest) != recorded_digest
        or report.get("workflow_id") != workflow_id
        or report.get("base_revision") != source_revision
        or report.get("workspace_head") != source_revision
        or report.get("run_status") != "accepted"
        or not isinstance(report.get("stage_statuses"), Mapping)
        or len(report.get("stage_statuses", {})) != 18
        or report.get("merge_or_publish_performed") is not False
        or report.get("production_deployment_performed") is not False
    ):
        raise RuntimeError("Vibe Coding host report failed canonical verification")
    return {
        "status": "completed",
        "workflow_id": workflow_id,
        "report_digest": recorded_digest,
        "workspace_head": report.get("workspace_head"),
        "changed_files": report.get("workspace_changed_files", []),
        "approval_receipts": [approval.approval_id],
        "evidence_refs": ["sha256:" + recorded_digest],
        "merge_or_publish_performed": False,
        "production_deployment_performed": False,
    }


def create_local_composition(
    project_root: str | Path,
    *,
    state_root: str | Path,
    provider_pool: Any | None = None,
) -> LocalRuntimeComposition:
    """Public factory-free local composition entry point."""
    return LocalRuntimeComposition(
        project_root, state_root=state_root, provider_pool=provider_pool,
    )


__all__ = [
    "LocalBudgetResolver", "LocalOperatorAuthority", "LocalProviderNotConfigured",
    "LocalProviderOperationLedger", "LocalProviderOutcomeUnknown",
    "LocalRuntimeComposition", "LocalTaskOutcome", "create_local_composition",
    "run_local_provider_task", "run_local_vibe_coding_task",
]
