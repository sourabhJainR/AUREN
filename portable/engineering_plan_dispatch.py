"""Crash-safe bridge from versioned engineering plans to the authoritative task queue.

A dispatch intent and its queued task are committed in the ExecutionController's
SQLite database in one transaction. The intent binds the plan/run revision and
the exact fresh-context packet digest. Replays return the original task; they
never enqueue a second task for the same run/task/revision.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4
from types import SimpleNamespace

from .engineering_readiness import (
    EngineeringBudgetLedger, EngineeringReadinessBlocked,
    EngineeringReadinessGate, department_for_role, infer_engineering_phase,
)

from .engineering_context_packet import EngineeringContextPacket
from .engineering_plan import EngineeringPlan, EngineeringPlanRun
from .engineering_plan_store import EngineeringPlanStore
from .workflow_profile import resolve_profile
from .workflow_profile_store import load_profile


class DispatchConflict(RuntimeError):
    """A dispatch key or plan revision was reused with different immutable inputs."""


class DispatchCancelled(RuntimeError):
    """Dispatch could not be cancelled safely in its current execution state."""


class EngineeringPlanDispatch:
    """Bind a verified plan revision and context packet to ExecutionController tasks."""

    def __init__(
        self, *, store: EngineeringPlanStore, controller: Any,
        recovery_bridge: Any | None = None,
    ) -> None:
        if recovery_bridge is None:
            raise ValueError("canonical recovery bridge is required for engineering-plan dispatch")
        if getattr(recovery_bridge, "store", None) is not store:
            raise ValueError("recovery bridge must validate the same plan store used for dispatch")
        self.store = store
        self.controller = controller
        self.recovery_bridge = recovery_bridge
        from .engineering_readiness import (
            EngineeringBudgetLedger, EngineeringReadinessGate,
        )
        self.readiness_gate = EngineeringReadinessGate(controller.progress_journal)
        self.budget_ledger = EngineeringBudgetLedger(controller.progress_journal)
        registry = getattr(controller, "_engineering_plan_recovery_bridges", None)
        if registry is None:
            registry = {}
            controller._engineering_plan_recovery_bridges = registry
        registry[str(store.database_path.resolve())] = recovery_bridge
        self.database_path = controller.db_path
        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_dispatch_intents (
                    dispatch_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_task_id TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    run_revision INTEGER NOT NULL,
                    source_revision TEXT NOT NULL,
                    packet_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(run_id, plan_task_id, run_revision),
                    UNIQUE(task_id)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dispatch_run ON engineering_dispatch_intents(run_id, created_at)"
            )

    def _discard_unqueued_work_unit(self, work_unit_id: str) -> None:
        """Remove a newly-created work unit when its dispatch loses a race.

        The unit is created before the task transaction because work units live in
        the company's separate SQLite database. Only untouched, still-running
        units with no durable task reference are eligible for cleanup.
        """
        with sqlite3.connect(self.database_path, timeout=10) as db:
            referenced = db.execute(
                "SELECT 1 FROM tasks WHERE work_unit_id=? LIMIT 1", (work_unit_id,)
            ).fetchone()
        if referenced is not None:
            return
        company_db_path = self.controller.company.db_path
        with sqlite3.connect(company_db_path, timeout=10) as db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            state = db.execute(
                "SELECT state,iteration FROM work_units WHERE id=?", (work_unit_id,)
            ).fetchone()
            if state == ("running", 0):
                db.execute("DELETE FROM events WHERE work_unit_id=?", (work_unit_id,))
                db.execute("DELETE FROM work_units WHERE id=? AND state='running' AND iteration=0", (work_unit_id,))
            db.commit()

    @staticmethod
    def _nonempty(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty text")
        return value.strip()

    @classmethod
    def _validate_payload_keys(cls, value: Any, path: str = "payload") -> None:
        """Reject credential-like keys recursively before durable queue serialization."""
        if isinstance(value, Mapping):
            forbidden = ("secret", "password", "api_key", "credential", "private_key")
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_").replace(" ", "_")
                if normalized == "token" or normalized.endswith("_token") or any(part in normalized for part in forbidden):
                    raise ValueError(f"{path} must not contain credential-like keys")
                cls._validate_payload_keys(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                cls._validate_payload_keys(item, f"{path}[{index}]")

    @staticmethod
    def _normalize_budget_binding(value):
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError('shared_budget_binding must be a mapping')
        required = {'budget_id', 'checkpoint_digest', 'policy_digest'}
        if not required.issubset(value):
            raise ValueError('shared budget binding requires budget_id, checkpoint_digest and policy_digest')
        result = {name: value[name] for name in required}
        for name, item in result.items():
            if not isinstance(item, str) or not item.strip():
                raise ValueError('shared budget fields must be non-empty text')
            result[name] = item.strip()
        for name in ('checkpoint_digest', 'policy_digest'):
            digest = result[name]
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('shared budget digests must be lowercase SHA-256')
        return result

    @staticmethod
    def _normalize_approval_evidence(value):
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError('approval_evidence must be a mapping')
        required = {'approval_id', 'receipt_digest', 'evidence_digest', 'policy_digest', 'approver_id', 'approver_role', 'decision'}
        allowed = required | {'implementer_principal_id', 'implementer_role_id'}
        if not required.issubset(value) or not set(value).issubset(allowed) or value.get('decision') != 'approved':
            raise ValueError('approval evidence must be a complete approved durable receipt')
        result = {name: value[name] for name in value}
        for name in required - {'decision'}:
            if not isinstance(result[name], str) or not result[name].strip():
                raise ValueError('approval evidence fields must be non-empty text')
        for name in ('implementer_principal_id', 'implementer_role_id'):
            if name in result and (
                not isinstance(result[name], str) or not result[name].strip()
                or len(result[name]) > 256 or not result[name].isprintable()
            ):
                raise ValueError('approval implementer identity fields must be bounded printable text')
        for name in ('receipt_digest', 'evidence_digest', 'policy_digest'):
            digest = result[name]
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('approval evidence digests must be lowercase SHA-256')
        return result

    def _bind_workflow_profile(self, payload: dict[str, Any]) -> None:
        """Bind the current host-owned profile exactly as ExecutionController.submit does."""
        task_profile = payload.pop("workflow_profile", None)
        payload.pop("_auren_workflow_profile", None)
        payload.pop("_auren_workflow_profile_digest", None)
        saved_profile = load_profile(self.controller.workflow_profile_path)
        effective_profile = resolve_profile(
            task=task_profile,
            saved=saved_profile.as_dict() if saved_profile is not None else None,
        )
        payload["_auren_workflow_profile"] = effective_profile.as_dict()
        payload["_auren_workflow_profile_digest"] = effective_profile.digest

    @staticmethod
    def _normalize_assignment_evidence(value):
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("assignment_evidence must be a mapping")
        base_required = {
            "schema_version", "assignment_id", "role_id", "manager_id", "action_class",
            "scope", "registry_digest", "policy_version", "policy_digest",
            "assignment_digest", "work_key", "task_payload_digest", "deadline_seconds",
            "authorization",
        }
        schema = value.get("schema_version")
        if schema == "auren-agent-assignment-v3":
            required = base_required
        elif schema == "auren-agent-assignment-v4":
            required = base_required | {"principal_id"}
        else:
            raise ValueError("assignment evidence has an unsupported schema")
        if set(value) != required:
            raise ValueError("assignment evidence is incomplete or has an unsupported schema")
        if schema == "auren-agent-assignment-v4" and (
            not isinstance(value.get("principal_id"), str)
            or not value["principal_id"].strip()
            or len(value["principal_id"]) > 256
            or not value["principal_id"].isprintable()
        ):
            raise ValueError("assignment principal identity is invalid")
        if value.get("authorization") != "host_authorized_before_submission":
            raise ValueError("assignment evidence lacks host authorization")
        for name in ("assignment_id", "role_id", "manager_id", "action_class", "policy_version", "work_key"):
            if not isinstance(value.get(name), str) or not value[name].strip():
                raise ValueError("assignment evidence identity fields must be non-empty text")
        scope = value.get("scope")
        if not isinstance(scope, list) or not scope or any(not isinstance(item, str) or not item.strip() for item in scope):
            raise ValueError("assignment evidence scope must be a non-empty string list")
        for name in ("registry_digest", "policy_digest", "assignment_digest", "task_payload_digest"):
            digest = value.get(name)
            if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError("assignment evidence digests must be lowercase SHA-256")
        deadline = value.get("deadline_seconds")
        if deadline is not None and (
            isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline) or not 0 < deadline <= 86400
        ):
            raise ValueError("assignment evidence deadline is invalid")
        return {name: value[name] for name in required}

    def _load_plan_state(self, run_id: str) -> tuple[EngineeringPlan, EngineeringPlanRun]:
        """Never load plan state for dispatch without its canonical checkpoint."""
        return self.recovery_bridge.load_verified(run_id)

    def _verified_state(
        self, run_id: str, task_id: str, packet: EngineeringContextPacket
    ) -> tuple[EngineeringPlan, EngineeringPlanRun]:
        if not isinstance(packet, EngineeringContextPacket):
            raise TypeError("an EngineeringContextPacket is required")
        packet.verify()
        plan, run = self._load_plan_state(run_id)
        run.verify_against(plan)
        if packet.run_id != run_id or packet.plan_digest != plan.digest:
            raise DispatchConflict("context packet is bound to a different plan")
        if packet.run_revision != run.revision:
            raise DispatchConflict("context packet run revision is stale")
        if packet.source_revision != plan.source_revision:
            raise DispatchConflict("context packet source revision mismatch")
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"task {task_id!r} is not in plan {run_id!r}")
        progress = next(item for item in run.tasks if item.task_id == task_id)
        if packet.task.get("task_id") != task_id or packet.task_status != progress.status:
            raise DispatchConflict("context packet task identity/status differs from persisted run")
        if progress.status not in {"ready", "running", "verifying"}:
            raise ValueError(f"task {task_id!r} is not dispatchable from {progress.status!r}")
        for dependency in task.depends_on:
            dependency_progress = next(item for item in run.tasks if item.task_id == dependency)
            if dependency_progress.status != "passed":
                raise ValueError(f"dependency {dependency!r} is not passed")
        for name, maximum in plan.budget.items():
            if name in packet.budget_remaining and packet.budget_remaining[name] > maximum:
                raise DispatchConflict(f"context packet inflates plan budget {name!r}")
        return plan, run

    def dispatch(
        self,
        *,
        run_id: str,
        task_id: str,
        dispatch_key: str,
        handler: str,
        packet: EngineeringContextPacket,
        payload: Mapping[str, Any] | None = None,
        goal: str | None = None,
        deadline_seconds: float | None = None,
        shared_budget_binding: Mapping[str, Any] | None = None,
        approval_evidence: Mapping[str, Any] | None = None,
        assignment_evidence: Mapping[str, Any] | None = None,
        _trusted_root_token: object | None = None,
    ):
        """Persist an idempotent dispatch intent before the handler can run.

        The caller must reuse dispatch_key on retries. Same-key/same-binding returns
        the original durable task. Any changed plan revision, packet, handler, or
        payload is rejected rather than silently replayed.
        """
        trusted_token = getattr(self.controller, "_trusted_plan_dispatch_token", None)
        root_required = getattr(self.controller, "_trusted_plan_dispatch_required", False)
        if (root_required and trusted_token is None) or (trusted_token is not None and _trusted_root_token is not trusted_token):
            raise PermissionError(
                "trusted plan dispatch composition root is configured; caller-supplied bindings are not accepted"
            )
        key = self._nonempty(dispatch_key, "dispatch_key")
        handler_name = self._nonempty(handler, "handler")
        if deadline_seconds is not None and (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 86400
        ):
            raise ValueError("deadline_seconds must be finite, greater than 0, and at most 86400")
        deadline_policy_seconds = float(deadline_seconds) if deadline_seconds is not None else None
        budget_binding = self._normalize_budget_binding(shared_budget_binding)
        approval_binding = self._normalize_approval_evidence(approval_evidence)
        assignment_binding = self._normalize_assignment_evidence(assignment_evidence)
        if root_required and assignment_binding is None:
            raise PermissionError("trusted plan dispatch requires a host-authorized specialist assignment")
        if root_required and approval_binding is not None and (
            "implementer_principal_id" not in approval_binding
            or "implementer_role_id" not in approval_binding
            or not isinstance(approval_binding["implementer_principal_id"], str)
            or not approval_binding["implementer_principal_id"].strip()
            or not isinstance(approval_binding["implementer_role_id"], str)
            or not approval_binding["implementer_role_id"].strip()
        ):
            raise PermissionError("trusted approval evidence must bind the authenticated implementer principal")

        # A retry must be able to retrieve its original task after the plan has
        # advanced. Validate the immutable packet/plan binding first, but do not
        # require the packet revision to equal the *current* run revision on replay.
        # Otherwise a successful dispatch becomes unrecoverable after any transition.
        if not isinstance(packet, EngineeringContextPacket):
            raise TypeError("an EngineeringContextPacket is required")
        packet.verify()
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError("dispatch payload must be a mapping")
        safe_payload = dict(payload or {})
        if "auren_dispatch" in safe_payload:
            raise ValueError("payload key 'auren_dispatch' is reserved for trusted dispatch metadata")
        if assignment_binding is not None:
            if safe_payload.get("_auren_agent_assignment") != assignment_binding:
                raise PermissionError("durable task assignment envelope differs from the trusted assignment binding")
            if safe_payload.get("work_key") != key:
                raise PermissionError("durable task work_key must equal the trusted dispatch identity")
        self._bind_workflow_profile(safe_payload)
        self._validate_payload_keys(safe_payload)
        encoded_payload = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            existing = db.execute(
                "SELECT * FROM engineering_dispatch_intents WHERE dispatch_key=?", (key,)
            ).fetchone()
        if existing is not None:
            plan, _current_run = self._load_plan_state(run_id)
            prior_task = self.controller.task(str(existing["task_id"]))
            if prior_task is None:
                raise DispatchConflict("existing readiness dispatch points to a missing durable task")
            prior_payload = dict(prior_task.payload)
            for reserved_name in (
                "_auren_engineering_task", "_auren_engineering_readiness",
                "_auren_engineering_readiness_digest", "_auren_engineering_phase",
                "_auren_engineering_department", "_auren_engineering_remediation",
            ):
                if reserved_name in prior_payload:
                    safe_payload[reserved_name] = prior_payload[reserved_name]
            if safe_payload.get("_auren_engineering_task") is True:
                self.readiness_gate.verify_payload({
                    **safe_payload, "work_key": key,
                })
            encoded_payload = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if (
                existing["run_id"] != run_id
                or existing["plan_task_id"] != task_id
                or existing["plan_digest"] != plan.digest
                or existing["source_revision"] != plan.source_revision
                or packet.run_id != run_id
                or packet.task.get("task_id") != task_id
                or packet.plan_digest != plan.digest
                or packet.source_revision != plan.source_revision
                or packet.run_revision != existing["run_revision"]
                or packet.packet_digest != existing["packet_digest"]
            ):
                raise DispatchConflict("idempotency key replay changed immutable plan or packet inputs")
            binding = {
                "run_id": run_id, "task_id": task_id, "plan_digest": plan.digest,
                "run_revision": int(existing["run_revision"]), "source_revision": plan.source_revision,
                "packet_digest": packet.packet_digest, "handler": handler_name,
                "deadline_policy_seconds": deadline_policy_seconds,
                "shared_budget_binding": budget_binding,
                "approval_evidence": approval_binding,
                "payload_digest": __import__("hashlib").sha256(encoded_payload.encode("utf-8")).hexdigest(),
            }
            if assignment_binding is not None:
                binding["assignment_evidence"] = assignment_binding
            return self._replay_or_conflict(existing, binding, handler_name, encoded_payload)

        plan, run = self._verified_state(run_id, task_id, packet)
        if any(name in safe_payload for name in (
            "_auren_engineering_readiness", "_auren_engineering_readiness_digest",
            "_auren_engineering_phase", "_auren_engineering_department",
        )):
            raise ValueError("engineering readiness fields are supervisor-owned")
        explicit_axes = safe_payload.pop("engineering_readiness", None)
        plan_task = next(item for item in plan.tasks if item.task_id == task_id)
        readiness = self.readiness_gate.assess_packet(
            work_key=key, plan=plan, task=plan_task, packet=packet,
            explicit_axes=explicit_axes if isinstance(explicit_axes, Mapping) else None,
        )
        if not readiness.ready:
            raise EngineeringReadinessBlocked(readiness)
        phase = infer_engineering_phase(handler_name, goal or "", plan_task.title)
        safe_payload.update({
            "work_key": key,
            "_auren_engineering_task": True,
            "_auren_engineering_readiness": readiness.to_dict(),
            "_auren_engineering_readiness_digest": readiness.assessment_digest,
            "_auren_engineering_phase": phase,
            "_auren_engineering_department": str(
                getattr(plan_task, "department", None) or department_for_role(
                    getattr(plan_task, "owner_role_id", None)
                )
            ),
            "_auren_engineering_remediation": list(getattr(plan_task, "remediation_steps", ()) or ()),
        })
        encoded_payload = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        execution_class = self.controller._handler_execution_class(handler_name)
        if execution_class not in {"read_only", "ci_gated", "consequential"}:
            raise ValueError("dispatch handler must have an explicit executable class")
        self.controller._load_handler(handler_name)
        binding = {
            "run_id": run_id, "task_id": task_id, "plan_digest": plan.digest,
            "run_revision": run.revision, "source_revision": plan.source_revision,
            "packet_digest": packet.packet_digest, "handler": handler_name,
            "deadline_policy_seconds": deadline_policy_seconds,
            "shared_budget_binding": budget_binding,
            "approval_evidence": approval_binding,
            "payload_digest": __import__("hashlib").sha256(encoded_payload.encode("utf-8")).hexdigest(),
        }
        if assignment_binding is not None:
            binding["assignment_evidence"] = assignment_binding
        binding_digest = __import__("hashlib").sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()

        # Fast replay path. The transaction below repeats this check to serialize races.
        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            existing = db.execute(
                "SELECT * FROM engineering_dispatch_intents WHERE dispatch_key=?", (key,)
            ).fetchone()
            if existing is not None:
                return self._replay_or_conflict(existing, binding, handler_name, encoded_payload)

        # Reject known duplicate/budget failures before creating even a bookkeeping work unit.
        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            collision = db.execute(
                "SELECT 1 FROM engineering_dispatch_intents WHERE run_id=? AND plan_task_id=? AND run_revision=?",
                (run_id, task_id, run.revision),
            ).fetchone()
            if collision is not None:
                # A concurrent identical dispatch may have committed after the
                # fast replay check. Prefer replaying that exact idempotency key;
                # only a different key for this run/task/revision is a conflict.
                existing = db.execute(
                    "SELECT * FROM engineering_dispatch_intents WHERE dispatch_key=?", (key,)
                ).fetchone()
                if existing is not None:
                    return self._replay_or_conflict(existing, binding, handler_name, encoded_payload)
                raise DispatchConflict("this plan task revision already has a different dispatch intent")
            dispatch_limit = plan.budget.get("dispatches")
            if dispatch_limit is not None:
                count = db.execute(
                    "SELECT COUNT(*) FROM engineering_dispatch_intents WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                if count >= dispatch_limit:
                    raise DispatchConflict("shared plan dispatch budget is exhausted")

        now = time.time()
        deadline_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=deadline_policy_seconds)).isoformat()
            if deadline_policy_seconds is not None else None
        )
        task_identifier = uuid4().hex
        task_payload = {
            **safe_payload,
            "auren_dispatch": {
                **binding, "dispatch_key": key, "binding_digest": binding_digest,
                "deadline_at": deadline_at,
                "plan_store_path": str(self.store.database_path.resolve()),
            },
        }
        task_json = json.dumps(task_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        timestamp = self.controller._now() if hasattr(self.controller, "_now") else __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM engineering_dispatch_intents WHERE dispatch_key=?", (key,)
            ).fetchone()
            if existing is not None:
                db.rollback()
                return self._replay_or_conflict(existing, binding, handler_name, encoded_payload)
            collision = db.execute(
                "SELECT * FROM engineering_dispatch_intents WHERE run_id=? AND plan_task_id=? AND run_revision=?",
                (run_id, task_id, run.revision),
            ).fetchone()
            if collision is not None:
                db.rollback()
                raise DispatchConflict("this plan task revision already has a different dispatch intent")
            dispatch_limit = plan.budget.get("dispatches")
            if dispatch_limit is not None:
                count = db.execute(
                    "SELECT COUNT(*) FROM engineering_dispatch_intents WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                if count >= dispatch_limit:
                    db.rollback()
                    raise DispatchConflict("shared plan dispatch budget is exhausted")
            try:
                # Hold the controller DB's write reservation while checking uniqueness
                # and creating the work unit. A concurrent loser blocks here and sees
                # the committed intent before it can create an orphan work unit.
                work = self.controller.company.start(goal or f"engineering plan {run_id}: {task_id}")
                db.execute(
                    "INSERT INTO tasks(id,handler,payload,work_key,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until,deadline_at,deadline_seconds) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_identifier, handler_name, task_json, key, "pending", 0, work.id,
                     timestamp, timestamp, timestamp, timestamp, deadline_at, deadline_policy_seconds),
                )
                db.execute(
                    "INSERT INTO engineering_dispatch_intents "
                    "(dispatch_key,run_id,task_id,plan_task_id,plan_digest,run_revision,source_revision,packet_digest,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (key, run_id, task_identifier, task_id, plan.digest, run.revision,
                     plan.source_revision, packet.packet_digest, "queued", now, now),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                self._discard_unqueued_work_unit(work.id)
                raise DispatchConflict("dispatch intent collided with a concurrent or prior dispatch") from exc

        task = self.controller.task(task_identifier)
        if task is None:
            raise RuntimeError("dispatch transaction committed but durable task could not be reloaded")
        self.controller._capture_event(
            "engineering_plan_task_dispatched", task, status="queued",
            metadata={
                "dispatch_key_digest": __import__("hashlib").sha256(key.encode("utf-8")).hexdigest(),
                "plan_digest": plan.digest, "run_revision": run.revision,
                "packet_digest": packet.packet_digest, "binding_digest": binding_digest,
            },
        )
        return task

    def _replay_or_conflict(self, existing: Mapping[str, Any], binding: Mapping[str, Any], handler: str, encoded_payload: str):
        try:
            task = self.controller.task(str(existing["task_id"]))
            if task is None:
                raise DispatchConflict("dispatch intent points to a missing durable task")
            task_payload = dict(task.payload)
            recorded = task_payload.get("auren_dispatch")
            if not isinstance(recorded, Mapping):
                raise DispatchConflict("durable task lacks its dispatch binding")
            expected = {
                "run_id": binding["run_id"], "task_id": binding["task_id"],
                "plan_digest": binding["plan_digest"], "run_revision": binding["run_revision"],
                "source_revision": binding["source_revision"], "packet_digest": binding["packet_digest"],
                "handler": handler,
                "deadline_policy_seconds": binding["deadline_policy_seconds"],
                "shared_budget_binding": binding["shared_budget_binding"],
                "approval_evidence": binding["approval_evidence"],
                "payload_digest": __import__("hashlib").sha256(encoded_payload.encode("utf-8")).hexdigest(),
            }
            if "assignment_evidence" in binding:
                expected["assignment_evidence"] = binding["assignment_evidence"]
            actual = {name: recorded.get(name) for name in expected}
            if actual != dict(expected):
                raise DispatchConflict("idempotency key replay changed immutable dispatch inputs")
            if (existing["run_id"] != binding["run_id"] or existing["plan_task_id"] != binding["task_id"]
                    or existing["plan_digest"] != binding["plan_digest"]
                    or existing["run_revision"] != binding["run_revision"]
                    or existing["source_revision"] != binding["source_revision"]
                    or existing["packet_digest"] != binding["packet_digest"]):
                raise DispatchConflict("dispatch intent binding mismatch")
            return task
        except (KeyError, TypeError) as exc:
            raise DispatchConflict("dispatch intent is malformed; explicit reconciliation is required") from exc

    def cancel(self, dispatch_key: str, *, reason: str) -> bool:
        """Cancel only a queued task; running side effects require cooperative recovery."""
        key = self._nonempty(dispatch_key, "dispatch_key")
        why = self._nonempty(reason, "reason")
        with sqlite3.connect(self.database_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            intent = db.execute(
                "SELECT task_id,status FROM engineering_dispatch_intents WHERE dispatch_key=?", (key,)
            ).fetchone()
            if intent is None:
                db.rollback()
                raise KeyError(f"dispatch intent {key!r} does not exist")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (intent["task_id"],)).fetchone()
            if task is None:
                db.rollback()
                raise DispatchConflict("dispatch intent points to a missing durable task")
            if task["state"] == "cancelled":
                db.commit()
                return True
            if task["state"] != "pending":
                db.rollback()
                raise DispatchCancelled(
                    f"task is {task['state']!r}; running or terminal work cannot be cancelled as queued work"
                )
            now = self.controller._now() if hasattr(self.controller, "_now") else str(time.time())
            db.execute(
                "UPDATE tasks SET state='cancelled',updated_at=?,lease_until=NULL,lease_token=NULL WHERE id=? AND state='pending'",
                (now, intent["task_id"]),
            )
            db.execute(
                "UPDATE engineering_dispatch_intents SET status='cancelled',updated_at=? WHERE dispatch_key=?",
                (time.time(), key),
            )
            db.commit()
        durable_task = self.controller.task(str(intent["task_id"]))
        if durable_task is not None:
            self.controller._capture_event(
                "engineering_plan_dispatch_cancelled", durable_task, status="cancelled",
                metadata={"phase": "engineering_plan_dispatch", "result_category": "operator_cancelled_queued_dispatch",
                          "retry_action": "cancel_queued_dispatch",
                          "dispatch_key_digest": __import__("hashlib").sha256(key.encode("utf-8")).hexdigest()},
            )
        return True


__all__ = ["DispatchConflict", "DispatchCancelled", "EngineeringPlanDispatch"]
