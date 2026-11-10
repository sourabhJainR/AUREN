"""Factory-free, single-user local runtime composition.

The local host owns identity, assignment, Guard policy, durable approvals,
bounded budgets, plan dispatch and provider invocation. MCP only admits work
through this composition; it never calls AdaptiveRuntime.run directly.
"""
from __future__ import annotations

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
from .engineering_context_packet import build_context_packet
from .engineering_plan import EngineeringPlan, PlanTask
from .engineering_plan_recovery import EngineeringPlanRecoveryBridge
from .engineering_plan_store import EngineeringPlanStore
from .organization_guard import (
    evaluate_guard_policy, guard_policy_fingerprint, load_guard_policy,
)
from .orchestration import Graph, Node, NodeKind
from .remote_model_providers import provider_pool_from_environment
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
        with sqlite3.connect(self.path, timeout=10) as db:
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
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,response_json,response_digest FROM provider_operations WHERE work_key=?",
                (work_key,),
            ).fetchone()
            if row is not None:
                db.commit()
                if row[0] == "completed":
                    response = json.loads(row[1])
                    if not isinstance(response, Mapping) or _digest(_canonical(response)) != row[2]:
                        raise LocalProviderOutcomeUnknown("stored provider response failed digest verification")
                    return dict(response)
                raise LocalProviderOutcomeUnknown(
                    "provider operation was already dispatched; automatic replay is blocked"
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
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute("PRAGMA busy_timeout=10000")
            cursor = db.execute(
                "UPDATE provider_operations SET status='completed',response_json=?,response_digest=?,updated_at=? "
                "WHERE work_key=? AND status='dispatching'",
                (encoded, digest, time.time(), work_key),
            )
            if cursor.rowcount != 1:
                raise LocalProviderOutcomeUnknown("provider operation state changed before its result was committed")

    def mark_unknown(self, work_key: str) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                "UPDATE provider_operations SET status='outcome_unknown',updated_at=? "
                "WHERE work_key=? AND status='dispatching'",
                (time.time(), work_key),
            )

    def response(self, work_key: str) -> Mapping[str, Any] | None:
        with sqlite3.connect(self.path, timeout=10) as db:
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


def run_local_provider_task(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Controller-owned, read-only provider task handler with durable replay fencing."""
    if not isinstance(payload, Mapping):
        raise TypeError("local provider payload must be a mapping")
    task = payload.get("task")
    work_key = payload.get("work_key")
    raw_state_root = payload.get("_auren_local_state_root")
    if (
        not isinstance(task, str) or not task.strip() or len(task) > 20_000
        or not isinstance(work_key, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{16,200}", work_key)
        or not isinstance(raw_state_root, str) or not Path(raw_state_root).is_absolute()
    ):
        raise ValueError("host-bound local provider task fields are invalid")
    supplied_state_root = Path(raw_state_root).expanduser()
    if supplied_state_root.is_symlink():
        raise PermissionError("local provider state root must not be a symlink")
    state_root = supplied_state_root.resolve()
    if not state_root.is_dir():
        raise PermissionError("local provider state root is unavailable or unsafe")
    with _PROVIDER_POOLS_LOCK:
        pool = _PROVIDER_POOLS.get(str(state_root))
    if pool is None:
        pool = provider_pool_from_environment()
    if pool is None:
        raise LocalProviderNotConfigured(
            "configure a direct API provider with AUREN_REMOTE_PROVIDER_ORDER, its API key and model ID"
        )
    remote_policy_digest = _remote_policy_digest()
    if not remote_policy_digest or payload.get("_auren_local_remote_policy_digest") != remote_policy_digest:
        raise LocalProviderNotConfigured("remote inference is not enabled for an explicitly public local workspace")
    providers = getattr(pool, "providers", ())
    if not providers:
        raise LocalProviderNotConfigured("configured provider adapter does not expose a reviewed provider identity")
    selected_provider = providers[0]
    selected_name = getattr(getattr(selected_provider, "spec", None), "name", None)
    selected_model = getattr(selected_provider, "model", None)
    binding = payload.get("_auren_local_provider")
    if (
        not isinstance(binding, Mapping)
        or binding.get("name") != selected_name
        or binding.get("model") != selected_model
    ):
        raise LocalProviderNotConfigured("configured provider selection changed after local task admission")
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
        response = pool(SimpleNamespace(
            task=prompt, timeout_seconds=120.0, provider_attempt=0,
        ))
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("configured provider returned no usable text")
        result = {
            "status": "completed",
            "provider": str(getattr(response, "provider", "unknown")),
            "model": str(getattr(response, "model", "unknown")),
            "content": content[:100_000],
            "usage": dict(getattr(response, "usage", {}) or {}) if isinstance(getattr(response, "usage", {}), Mapping) else {},
            "response_digest": str(getattr(response, "response_digest", "")),
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
        with sqlite3.connect(self.path, timeout=10) as db:
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
        with sqlite3.connect(self.path, timeout=10) as db:
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
        with sqlite3.connect(self.path, timeout=10) as db:
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
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
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

        self.operator = LocalOperatorAuthority(
            self.state_root / "operator.sqlite3",
            self.project_root,
            self.state_root / "adaptive-runtime" / "executive" / ".auren" / "guard_policy.json",
        )
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
        return self.provider_pool is not None

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

        if self.provider_pool is None:
            raise LocalProviderNotConfigured(
                "no provider is configured; set AUREN_REMOTE_PROVIDER_ORDER and the selected provider key/model before submitting tasks"
            )
        remote_policy_digest = _remote_policy_digest()
        if not remote_policy_digest:
            raise LocalProviderNotConfigured(
                "remote inference is disabled by default; explicitly set AUREN_LOCAL_REMOTE_INFERENCE=enabled "
                "and AUREN_LOCAL_DATA_CLASSIFICATION=public only for work approved for external processing"
            )
        source_revision = self._source_revision()
        providers = getattr(self.provider_pool, "providers", ())
        if not providers:
            raise LocalProviderNotConfigured("configured provider pool has no reviewed provider adapter")
        selected = providers[0]
        name = getattr(getattr(selected, "spec", None), "name", None)
        model = getattr(selected, "model", None)
        if not isinstance(name, str) or not name or not isinstance(model, str) or not model:
            raise LocalProviderNotConfigured("configured provider identity or model is incomplete")
        return {
            "_auren_local_source_revision": source_revision,
            "_auren_local_provider": {"name": name, "model": model},
            "_auren_local_policy_digest": _digest(
                guard_policy_fingerprint(policy) + "|" + remote_policy_digest
            ),
            "_auren_local_remote_policy_digest": remote_policy_digest,
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
        return EngineeringPlan(
            run_id="local-" + trigger_id,
            goal=task.strip(),
            requirements=(task.strip(),),
            tasks=(PlanTask(
                task_id="execute",
                title=task.strip(),
                acceptance_criteria=(
                    "The configured provider returns a non-empty response.",
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

    def _mark_plan_complete(self, run_id: str, evidence_ref: str) -> None:
        _plan, run = self.recovery_bridge.load_verified(run_id)
        status = run.tasks[0].status
        if status == "passed":
            return
        if status == "ready":
            run = self.recovery_bridge.transition_task(
                run_id, "execute", "running", reason="canonical controller task started",
            )
            status = "running"
        if status == "running":
            run = self.recovery_bridge.transition_task(
                run_id, "execute", "verifying", reason="provider result returned by canonical controller",
            )
            status = "verifying"
        if status == "verifying":
            self.recovery_bridge.transition_task(
                run_id, "execute", "passed",
                reason="provider response and durable digest verified",
                evidence_refs=(evidence_ref,),
                next_safe_action="goal_acceptance_evidence_recorded",
            )

    def run_trigger(self, request: AdaptiveTriggerRequest, trigger_id: str) -> LocalTaskOutcome:
        principal_digest = request.context.get("_auren_mcp_subject")
        if not self.operator.verify_digest(str(principal_digest or "")):
            raise PermissionError("persisted MCP event is not bound to the host-owned local operator")
        if self.provider_pool is None:
            raise LocalProviderNotConfigured("provider configuration is unavailable")
        source_revision = request.context.get("_auren_local_source_revision")
        provider_binding = request.context.get("_auren_local_provider")
        policy_digest = request.context.get("_auren_local_policy_digest")
        remote_policy_digest = _remote_policy_digest()
        current_policy = load_guard_policy(self.controller.root / ".auren" / "guard_policy.json")
        if (
            not isinstance(source_revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
            or not isinstance(provider_binding, Mapping)
            or not isinstance(policy_digest, str)
            or not remote_policy_digest
            or request.context.get("_auren_local_remote_policy_digest") != remote_policy_digest
            or policy_digest != _digest(guard_policy_fingerprint(current_policy) + "|" + remote_policy_digest)
        ):
            raise PermissionError("persisted MCP event lacks current host-bound source, provider or remote-data policy evidence")
        plan = self._plan_for(trigger_id, request.task, source_revision, policy_digest)
        dispatch_key = "local-mcp:" + trigger_id
        try:
            stored_plan, run = self.plan_store.load(plan.run_id)
            if stored_plan.digest != plan.digest:
                raise PermissionError("stable local task ID was reused for different plan content or source revision")
        except KeyError:
            pass
        run = self.recovery_bridge.start(plan)
        if run.tasks[0].status == "passed":
            existing_task = self.controller.task_by_work_key(dispatch_key)
            response = LocalProviderOperationLedger(
                _provider_ledger_path(self.state_root)
            ).response(dispatch_key)
            if existing_task is None or existing_task.state != "completed" or response is None:
                raise RuntimeError("passed local plan is missing its completed controller task or provider receipt")
            evidence = run.tasks[0].evidence_refs
            return LocalTaskOutcome("completed", existing_task.id, evidence, "already_verified")
        if run.tasks[0].status == "pending":
            run = self.recovery_bridge.transition_task(
                plan.run_id, "execute", "ready", reason="local operator and provider admission passed",
                next_safe_action="dispatch_provider_task",
            )
        elif run.tasks[0].status in {"blocked", "failed", "cancelled"}:
            return LocalTaskOutcome(
                "blocked", "local-" + trigger_id, run.tasks[0].evidence_refs,
                "explicit operator reauthorization or replanning is required",
            )

        packet = build_context_packet(
            plan=plan,
            run=run,
            task_id="execute",
            policy_constraints=(
                "Provider inference only; this handler has no repository write tools.",
                "No merge, release, deployment, publication or other external action is authorized.",
                "The canonical ExecutionController and current Guard policy remain authoritative.",
            ),
            budget_remaining={"attempts": 1, "steps": 8, "tool_calls": 1, "tokens": 4096},
            next_safe_action="dispatch_read_only_provider_inference",
            max_bytes=16_384,
        )
        payload = {
            "task": request.task.strip(),
            "resource": "task-intent",
            "work_key": dispatch_key,
            "_auren_local_state_root": str(self.state_root),
            "_auren_local_provider": dict(provider_binding),
            "_auren_local_policy_digest": policy_digest,
            "_auren_local_remote_policy_digest": remote_policy_digest,
        }
        task = self.dispatch_root.dispatch(
            run_id=plan.run_id,
            task_id="execute",
            dispatch_key=dispatch_key,
            handler=_HANDLER,
            packet=packet,
            payload=payload,
            goal=plan.goal,
            deadline_seconds=300,
        )
        deadline = time.monotonic() + 330.0
        while time.monotonic() < deadline:
            current = self.controller.task(task.id)
            if current is None:
                raise RuntimeError("canonical controller lost the dispatched local task")
            if current.state == "completed":
                response = LocalProviderOperationLedger(
                    _provider_ledger_path(self.state_root)
                ).response(dispatch_key)
                if not isinstance(response, Mapping):
                    raise RuntimeError("completed provider task has no verified durable response")
                digest = str(response.get("response_digest") or _digest(_canonical(response)))
                evidence_ref = "sha256:" + digest if re.fullmatch(r"[0-9a-f]{64}", digest) else "sha256:" + _digest(_canonical(response))
                self._mark_plan_complete(plan.run_id, evidence_ref)
                return LocalTaskOutcome("completed", task.id, (evidence_ref,), "acceptance_evidence_recorded")
            if current.state in {"failed", "cancelled", "blocked_approval"}:
                try:
                    _stored_plan, current_run = self.recovery_bridge.load_verified(plan.run_id)
                    if current_run.tasks[0].status == "ready":
                        self.recovery_bridge.transition_task(
                            plan.run_id, "execute", "blocked",
                            reason="canonical controller did not complete the provider task",
                        )
                except Exception:
                    pass
                return LocalTaskOutcome(
                    "blocked", task.id, (),
                    "inspect controller evidence; provider operations with unknown outcomes are not replayed",
                )
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
                        "blocked", task.id, (),
                        "continuous supervisor is stopped or requires an explicit operator recovery action",
                    )
            time.sleep(0.2)
        return LocalTaskOutcome(
            "blocked", task.id, (),
            "controller task is still active; wait for its durable lease and status to reconcile",
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
