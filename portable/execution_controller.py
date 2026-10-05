"""Durable default execution controller for AUREN.

This is the process boundary between graph/team execution and long-lived work.
Task handlers are registered by importable reference so a fresh service process
can reconstruct them after a restart. Handler state and outcomes live in SQLite.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .autonomous_company import AutonomousCompany, TrustScore

Handler = Callable[[Mapping[str, Any]], Any]

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class DurableTask:
    id: str
    handler: str
    payload: dict[str, Any]
    state: str
    attempts: int
    work_unit_id: str

class ExecutionController:
    """Durable supervisor used as the default owner of graph/team execution."""

    def __init__(self, root: str | Path, *, company: AutonomousCompany | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.company = company or AutonomousCompany(self.root)
        self.db_path = self.root / ".auren" / "execution.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS handlers(
              name TEXT PRIMARY KEY, reference TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              work_unit_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT, lease_until TEXT);
            """)

    def register_handler(self, name: str, handler: Handler | str) -> str:
        """Persist an importable handler reference; callable handlers use module:qualname."""
        if not name.strip():
            raise ValueError("handler name is required")
        if isinstance(handler, str):
            reference = handler
        else:
            module = getattr(handler, "__module__", None)
            qualname = getattr(handler, "__qualname__", None)
            if not module or not qualname or "<locals>" in qualname:
                raise ValueError("durable handlers must be importable module-level callables")
            reference = f"{module}:{qualname}"
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO handlers(name,reference,updated_at) VALUES(?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET reference=excluded.reference,updated_at=excluded.updated_at",
                       (name, reference, _now()))
        return reference

    def _load_handler(self, name: str) -> Handler:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT reference FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        module_name, qualname = str(row[0]).split(":", 1)
        obj: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        if not callable(obj):
            raise TypeError(f"durable handler is not callable: {row[0]}")
        return obj

    def work_unit_for_session(self, session_id: str, goal: str) -> str:\n        with sqlite3.connect(self.db_path) as db:\n            row = db.execute("SELECT work_unit_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()\n            if row:\n                return str(row[0])\n        work = self.company.start(goal)\n        with sqlite3.connect(self.db_path) as db:\n            db.execute("INSERT INTO sessions(session_id,work_unit_id,goal,updated_at) VALUES(?,?,?,?)", (session_id, work.id, goal, _now()))\n        return work.id\n\n    def submit(self, handler: str, payload: Mapping[str, Any], *, goal: str | None = None) -> DurableTask:
        self._load_handler(handler)
        work = self.company.start(goal or f"execute durable handler: {handler}")
        task = DurableTask(uuid4().hex, handler, dict(payload), "pending", 0, work.id)
        with sqlite3.connect(self.db_path) as db:
            now = _now()
            db.execute("INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (task.id, task.handler, json.dumps(task.payload, sort_keys=True),
                        task.state, task.attempts, task.work_unit_id, now, now, now, now))
        return task

    def _claim(self, task_id: str) -> DurableTask | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks WHERE id=?",
                             (task_id,)).fetchone()
            if not row or row[3] in {"completed", "failed", "cancelled"}:
                return None
            attempts = int(row[4]) + 1
            db.execute("UPDATE tasks SET state='running',attempts=?,updated_at=? WHERE id=?",
                       (attempts, _now(), task_id))
        return DurableTask(row[0], row[1], json.loads(row[2]), "running", attempts, row[5])

    def run_once(self, task_id: str) -> TrustScore | None:
        task = self._claim(task_id)
        if task is None:
            return None
        handler = self._load_handler(task.handler)
        try:
            result = handler(task.payload)
            evidence = [f"handler:{task.handler}"]
            if isinstance(result, Mapping):
                evidence.extend(str(x) for x in result.get("evidence", ()) if x)
                success = bool(result.get("success", True))
                detail = str(result.get("detail", "handler completed"))
            else:
                success, detail = True, "handler completed"
            self.company.record(task.work_unit_id, "execution", "completed" if success else "failed", detail, evidence)
            state = "completed" if success else "failed"
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE tasks SET state=?,updated_at=? WHERE id=?", (state, _now(), task.id))
            return self.company.finish(task.work_unit_id, success=success, reason=detail, evidence=evidence)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.company.record(task.work_unit_id, "execution", "failed", detail, (f"handler:{task.handler}",))
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE tasks SET state=?,updated_at=? WHERE id=?", ("failed", _now(), task.id))
            self.company.finish(task.work_unit_id, success=False, reason=detail)
            raise

    def resume_pending(self, *, limit: int = 20) -> list[TrustScore]:
        """Resume pending/running work after process or host restart."""
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT id FROM tasks WHERE state IN ('pending','running') ORDER BY updated_at LIMIT ?",
                              (limit,)).fetchall()
        scores = []
        for (task_id,) in rows:
            score = self.run_once(task_id)
            if score is not None:
                scores.append(score)
        return scores


    def heartbeat(self, task_id: str, *, lease_seconds: int = 300) -> None:
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=max(1, lease_seconds))).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE tasks SET heartbeat_at=?,lease_until=?,updated_at=? WHERE id=? AND state='running'",
                       (now.isoformat(), lease, now.isoformat(), task_id))

    def detect_stale(self, stale_after_seconds: int = 900) -> list[DurableTask]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))).isoformat()
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks WHERE state='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)", (cutoff,)).fetchall()
        return [DurableTask(r[0],r[1],json.loads(r[2]),r[3],int(r[4]),r[5]) for r in rows]

    def recover_stale(self, task_id: str, *, reason: str) -> None:
        task = self.task(task_id)
        if task is None:
            return
        self.company.record(task.work_unit_id, "recovery", "detected", reason, (f"task:{task.id}",))
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE tasks SET state='pending',updated_at=?,lease_until=NULL WHERE id=? AND state='running'", (_now(), task_id))

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
            rows = db.execute("SELECT id,work_unit_id,state,attempts FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        for task_id,wid,state,attempts in rows:
            ok = state != "failed" and attempts < 1000000
            self.company.record(wid, "self_review", "passed" if ok else "failed",
                                f"task={task_id}; state={state}; attempts={attempts}", (f"task:{task_id}",))
            count += 1
        return count

    def health_findings(self) -> tuple[str, ...]:
        findings = []
        with sqlite3.connect(self.db_path) as db:
            failed = db.execute("SELECT COUNT(*) FROM tasks WHERE state='failed'").fetchone()[0]
            stale = db.execute("SELECT COUNT(*) FROM tasks WHERE state='running' AND (heartbeat_at IS NULL OR heartbeat_at<?)",
                               ((datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(),)).fetchone()[0]
        if failed:
            findings.append(f"failed_tasks={failed}")
        if stale:
            findings.append(f"stale_tasks={stale}")
        return tuple(findings)

    def task(self, task_id: str) -> DurableTask | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT id,handler,payload,state,attempts,work_unit_id FROM tasks WHERE id=?",
                             (task_id,)).fetchone()
        return DurableTask(row[0], row[1], json.loads(row[2]), row[3], int(row[4]), row[5]) if row else None

    def trust(self, task_id: str) -> TrustScore:
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return self.company.trust(task.work_unit_id)

    def execute_graph(self, graph: Any, task_id: str, intent: str, context: Mapping[str, Any] | None = None) -> Any:
        """Compatibility adapter for StateGraph/GraphAgentTeam-style callers."""
        from .orchestration import Orchestrator
        return Orchestrator(graph).run(task_id, intent, context)

__all__ = ["ExecutionController", "DurableTask", "Handler"]
