"""Crash-safe bridge from versioned engineering plans to the authoritative task queue.

A dispatch intent and its queued task are committed in the ExecutionController's
SQLite database in one transaction. The intent binds the plan/run revision and
the exact fresh-context packet digest. Replays return the original task; they
never enqueue a second task for the same run/task/revision.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Mapping
from uuid import uuid4

from .engineering_context_packet import EngineeringContextPacket
from .engineering_plan import EngineeringPlan, EngineeringPlanRun
from .engineering_plan_store import EngineeringPlanStore


class DispatchConflict(RuntimeError):
    """A dispatch key or plan revision was reused with different immutable inputs."""


class DispatchCancelled(RuntimeError):
    """Dispatch could not be cancelled safely in its current execution state."""


class EngineeringPlanDispatch:
    """Bind a verified plan revision and context packet to ExecutionController tasks."""

    def __init__(self, *, store: EngineeringPlanStore, controller: Any) -> None:
        self.store = store
        self.controller = controller
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

    @staticmethod
    def _nonempty(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty text")
        return value.strip()

    def _verified_state(
        self, run_id: str, task_id: str, packet: EngineeringContextPacket
    ) -> tuple[EngineeringPlan, EngineeringPlanRun]:
        if not isinstance(packet, EngineeringContextPacket):
            raise TypeError("an EngineeringContextPacket is required")
        packet.verify()
        plan, run = self.store.load(run_id)
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
    ):
        """Persist an idempotent dispatch intent before the handler can run.

        The caller must reuse dispatch_key on retries. Same-key/same-binding returns
        the original durable task. Any changed plan revision, packet, handler, or
        payload is rejected rather than silently replayed.
        """
        key = self._nonempty(dispatch_key, "dispatch_key")
        handler_name = self._nonempty(handler, "handler")
        plan, run = self._verified_state(run_id, task_id, packet)
        execution_class = self.controller._handler_execution_class(handler_name)
        if execution_class not in {"read_only", "ci_gated", "consequential"}:
            raise ValueError("dispatch handler must have an explicit executable class")
        self.controller._load_handler(handler_name)
        safe_payload = dict(payload or {})
        if any(str(name).lower() in {"token", "password", "api_key", "secret", "private_key"} for name in safe_payload):
            raise ValueError("dispatch payload must not contain credential-like top-level keys")
        encoded_payload = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        binding = {
            "run_id": run_id, "task_id": task_id, "plan_digest": plan.digest,
            "run_revision": run.revision, "source_revision": plan.source_revision,
            "packet_digest": packet.packet_digest, "handler": handler_name,
            "payload_digest": __import__("hashlib").sha256(encoded_payload.encode("utf-8")).hexdigest(),
        }
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

        work = self.controller.company.start(goal or f"engineering plan {run_id}: {task_id}")
        now = time.time()
        task_identifier = uuid4().hex
        task_payload = {
            **safe_payload,
            "auren_dispatch": {**binding, "dispatch_key": key, "binding_digest": binding_digest},
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
                db.execute(
                    "INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (task_identifier, handler_name, task_json, "pending", 0, work.id,
                     timestamp, timestamp, timestamp, timestamp),
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
                "payload_digest": __import__("hashlib").sha256(encoded_payload.encode("utf-8")).hexdigest(),
            }
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
