"""Factory-free, single-user local runtime composition.

The local host owns identity, assignment, Guard policy, durable approvals,
bounded budgets, plan dispatch and provider invocation. MCP only admits work
through this composition; it never calls AdaptiveRuntime.run directly.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .adaptive_runtime import AdaptiveRuntime
from .adaptive_trigger import AdaptiveTrigger, AdaptiveTriggerRequest
from .agent_org_runtime import REGISTRY_PATH, authorize_agent_assignment
from .durable_approval import DurableApprovalStore
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


_OPERATOR_ID = "local-operator"
_LOCAL_ISSUER = "auren-local"
_HANDLER = "local_provider_task"
_HANDLER_ACTION = "propose_changes"
_ASSIGNMENT_SCOPE = ("task-intent",)
_PROVIDER_POOLS: dict[str, Any] = {}
_PROVIDER_POOLS_LOCK = threading.RLock()


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
        return bool(
            approver_id == _OPERATOR_ID
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
        policy_digest = _digest("auren-local-budget-v1|max_steps=8|max_attempts=4|deadline=300")
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
                guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4, deadline_seconds=300)
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
        self.plan_store = EngineeringPlanStore(self.state_root / "engineering-plans.sqlite3")
        self.recovery_bridge = EngineeringPlanRecoveryBridge(
            store=self.plan_store, ledger=self.runtime.recovery_ledger,
        )
        self.approval_store = DurableApprovalStore(
            self.controller,
            approver_authorizer=self.operator.authorize_approver,
            context_provider=self.operator.approval_context,
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
        self._closed = False

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

    def admit_task(
        self, principal: Any, task: str, context: Mapping[str, Any], event_id: str,
    ) -> Mapping[str, Any]:
        if not self.operator.verify_principal(principal):
            raise PermissionError("local operator identity is not authorized for this workspace")
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
            raise PermissionError("current Guard policy denied local task admission: " + decision.reason)

        # A caller retry must reuse the original host-bound source/provider metadata.
        # Re-resolving either value after a workspace or configuration change would
        # turn the same idempotency key into a different task intent.
        existing = self.runtime.trigger_runtime.get_event(event_id)
        if existing is not None:
            if existing.payload.get("task") != task.strip():
                raise PermissionError("idempotency key is already bound to different task content")
            existing_context = existing.payload.get("context", {})
            if (
                not isinstance(existing_context, Mapping)
                or existing_context.get("_auren_mcp_subject") != self.operator.principal_digest
                or not isinstance(existing_context.get("_auren_local_source_revision"), str)
                or not isinstance(existing_context.get("_auren_local_provider"), Mapping)
                or not isinstance(existing_context.get("_auren_local_policy_digest"), str)
                or not isinstance(existing_context.get("_auren_local_remote_policy_digest"), str)
            ):
                raise PermissionError("existing task is missing its immutable host admission binding")
            return {
                "_auren_local_source_revision": existing_context["_auren_local_source_revision"],
                "_auren_local_provider": dict(existing_context["_auren_local_provider"]),
                "_auren_local_policy_digest": existing_context["_auren_local_policy_digest"],
                "_auren_local_remote_policy_digest": existing_context["_auren_local_remote_policy_digest"],
            }

        remote_policy_digest = _remote_policy_digest()
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
        return {
            "_auren_local_source_revision": source_revision,
            "_auren_local_provider": provider_binding,
            "_auren_local_policy_digest": _digest(
                guard_policy_fingerprint(policy) + "|" + bound_remote_policy_digest
            ),
            "_auren_local_remote_policy_digest": bound_remote_policy_digest,
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
    ) -> EngineeringPlan:
        if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
            raise ValueError("local task is missing its host-bound Git source revision")
        contract = RequirementContractEngine().build(
            task.strip(),
            (Requirement(
                requirement_id="R1", statement=task.strip(),
                acceptance_criteria=(
                    "The admission-bound local or remote model returns a non-empty response.",
                    "The response digest is durably recorded and replay-safe.",
                ),
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
                acceptance_criteria=(
                    "The admission-bound local or remote model returns a non-empty response.",
                    "The response digest is durably recorded and replay-safe.",
                ),
                rollback_plan="This provider task is read-only and must not modify the repository.",
                risk="low",
                owner_role_id="BE-01",
                manager_role_id="EM-ENG-01",
            ),),
            source_revision=source_revision,
            policy_digest=policy_digest,
            budget={"attempts": 1, "dispatches": 1, "tokens": 4096, "wall_seconds": 300},
        )

    def _goal_reconciler_for(
        self,
        plan: EngineeringPlan,
        provider_binding: Mapping[str, Any],
        policy_digest: str,
        remote_policy_digest: str,
    ) -> EngineeringPlanGoalReconciler:
        holder: dict[str, EngineeringPlanGoalReconciler] = {}

        def payload_resolver(_plan: EngineeringPlan, task: PlanTask) -> Mapping[str, Any]:
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
                "steps": 1,
                "tool_calls": 1,
                "tokens": min(4096, int(current_plan.budget.get("tokens", 4096))),
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
            evidence_ref = "sha256:" + response_digest
            return {"verified": True, "evidence_refs": (evidence_ref,)}

        reconciler = EngineeringPlanGoalReconciler(
            run_id=plan.run_id,
            controller=self.controller,
            recovery_bridge=self.recovery_bridge,
            trusted_dispatch_root=self.dispatch_root,
            handler_resolver=lambda _plan, _task: _HANDLER,
            payload_resolver=payload_resolver,
            budget_resolver=budget_resolver,
            completion_verifier=completion_verifier,
            policy_constraints=(
                "Provider inference is proposal-only and has no repository write tools.",
                "No merge, release, deployment, publication or other external action is authorized.",
                "The canonical ExecutionController and current Guard policy remain authoritative.",
            ),
        )
        holder["reconciler"] = reconciler
        self.supervisor.goal_reconcilers[reconciler.goal_key] = reconciler
        return reconciler

    def run_trigger(self, request: AdaptiveTriggerRequest, trigger_id: str) -> LocalTaskOutcome:
        principal_digest = request.context.get("_auren_mcp_subject")
        if not self.operator.verify_digest(str(principal_digest or "")):
            raise PermissionError("persisted MCP event is not bound to the host-owned local operator")
        source_revision = request.context.get("_auren_local_source_revision")
        provider_binding = request.context.get("_auren_local_provider")
        policy_digest = request.context.get("_auren_local_policy_digest")
        remote_policy_digest = _remote_policy_digest()
        current_policy = load_guard_policy(self.controller.root / ".auren" / "guard_policy.json")
        provider_kind = provider_binding.get("kind", "remote") if isinstance(provider_binding, Mapping) else None
        bound_remote_policy_digest = remote_policy_digest if provider_kind == "remote" else ""
        if (
            not isinstance(source_revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
            or not isinstance(provider_binding, Mapping)
            or provider_kind not in {"local", "remote"}
            or not isinstance(policy_digest, str)
            or request.context.get("_auren_local_remote_policy_digest") != bound_remote_policy_digest
            or (provider_kind == "remote" and not remote_policy_digest)
            or policy_digest != _digest(
                guard_policy_fingerprint(current_policy) + "|" + bound_remote_policy_digest
            )
        ):
            raise PermissionError("persisted MCP event lacks current host-bound source, provider or model-policy evidence")

        plan = self._plan_for(trigger_id, request.task, source_revision, policy_digest)
        try:
            stored_plan, _stored_run = self.plan_store.load(plan.run_id)
            if stored_plan.digest != plan.digest:
                raise PermissionError("stable local task ID was reused for different plan content or source revision")
        except KeyError:
            pass
        self.recovery_bridge.start(plan)
        reconciler = self._goal_reconciler_for(
            plan, provider_binding, policy_digest, remote_policy_digest,
        )
        deadline = time.monotonic() + 330.0
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
                response = LocalProviderOperationLedger(
                    _provider_ledger_path(self.state_root)
                ).response(work_key)
                if durable is None or durable.state != "completed" or response is None:
                    raise RuntimeError("passed local goal is missing its completed controller task or provider receipt")
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
        self._closed = True


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
    "run_local_provider_task",
]
