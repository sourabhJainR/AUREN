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
from .engineering_recovery import verify_ci_gate

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
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, goal TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handlers(
              name TEXT PRIMARY KEY, reference TEXT NOT NULL, updated_at TEXT NOT NULL,
              ci_gate_policy_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              work_unit_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT, lease_until TEXT);
            """)
            handler_columns = {row[1] for row in db.execute("PRAGMA table_info(handlers)").fetchall()}
            if "ci_gate_policy_json" not in handler_columns:
                db.execute("ALTER TABLE handlers ADD COLUMN ci_gate_policy_json TEXT NOT NULL DEFAULT '{}'")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            for column in ("heartbeat_at", "lease_until"):
                if column not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")


    def register_handler(
        self, name: str, handler: Handler | str, *,
        ci_gate_policy: Mapping[str, Any] | None = None,
    ) -> str:
        """Register a handler and optional trusted CI gate policy.

        CI policy is set by the service/operator at handler registration, never by
        task payload. A protected handler's tasks cannot disable the gate or
        replace the expected SHA/required checks in their input.
        """
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
        policy = dict(ci_gate_policy or {})
        if policy:
            expected_sha = policy.get("expected_head_sha")
            required_checks = policy.get("required_checks")
            if not isinstance(expected_sha, str) or not expected_sha.strip():
                raise ValueError("CI gate policy requires a trusted expected_head_sha")
            if (not isinstance(required_checks, (list, tuple)) or not required_checks
                    or not all(isinstance(item, str) and item.strip() for item in required_checks)):
                raise ValueError("CI gate policy requires non-empty trusted required_checks")
            policy = {
                "expected_head_sha": expected_sha,
                "required_checks": list(dict.fromkeys(required_checks)),
            }
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO handlers(name,reference,updated_at,ci_gate_policy_json) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET reference=excluded.reference,updated_at=excluded.updated_at,"
                "ci_gate_policy_json=excluded.ci_gate_policy_json",
                (name, reference, _now(), json.dumps(policy, sort_keys=True)),
            )
        return reference

    def _handler_ci_gate_policy(self, name: str) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT ci_gate_policy_json FROM handlers WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(f"unknown durable handler: {name}")
        policy = json.loads(row[0] or "{}")
        if not isinstance(policy, dict):
            raise ValueError(f"invalid persisted CI gate policy for handler: {name}")
        return policy

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

    def work_unit_for_session(self, session_id: str, goal: str) -> str:
        """Return a stable work unit for a session, creating it once if needed."""
        if not session_id.strip():
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
            now = _now()
            lease = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            db.execute("UPDATE tasks SET state='running',attempts=?,updated_at=?,heartbeat_at=?,lease_until=? WHERE id=?",
                       (attempts, now, now, lease, task_id))
        return DurableTask(row[0], row[1], json.loads(row[2]), "running", attempts, row[5])

    def run_once(self, task_id: str) -> TrustScore | None:
        task = self._claim(task_id)
        if task is None:
            return None
        handler = self._load_handler(task.handler)
        try:
            self.heartbeat(task.id)
            result = handler(task.payload)
            self.heartbeat(task.id)
            evidence = [f"handler:{task.handler}"]
            if isinstance(result, Mapping):
                evidence.extend(str(x) for x in result.get("evidence", ()) if x)
                success = bool(result.get("success", True))
                detail = str(result.get("detail", "handler completed"))
            else:
                success, detail = True, "handler completed"

            # The trusted handler registration owns gate policy. Task payload cannot
            # disable the gate, lower required checks, or substitute the expected SHA.
            gate_policy = self._handler_ci_gate_policy(task.handler)
            if gate_policy:
                gate = result.get("ci_gate") if isinstance(result, Mapping) else None
                if not isinstance(gate, Mapping):
                    success = False
                    detail = "required exact-head CI evidence is missing"
                    evidence.append("ci-gate:missing")
                else:
                    try:
                        passed_checks = gate.get("passed_checks", ())
                        skipped_checks = gate.get("skipped_checks", ())
                        if not all(isinstance(items, (list, tuple)) and all(isinstance(x, str) for x in items)
                                   for items in (passed_checks, skipped_checks)):
                            raise ValueError("check lists must be arrays of strings")
                        verdict = verify_ci_gate(
                            expected_head_sha=gate_policy["expected_head_sha"],
                            ci_head_sha=str(gate.get("head_sha", "")),
                            status=str(gate.get("status", "")),
                            conclusion=str(gate.get("conclusion", "")) or None,
                            required_checks=gate_policy["required_checks"],
                            passed_checks=passed_checks,
                            skipped_checks=skipped_checks,
                        )
                    except (KeyError, TypeError, ValueError) as gate_error:
                        verdict = None
                        gate_reason = f"malformed exact-head CI evidence: {gate_error}"
                    if verdict is None:
                        success = False
                        detail = gate_reason
                    elif not verdict.accepted:
                        success = False
                        detail = f"exact-head CI gate rejected task: {verdict.reason}"
                    else:
                        evidence.append(f"ci-head:{gate.get('head_sha')}")
                        evidence.append("ci-gate:required-checks-passed")
                        detail = f"{detail}; {verdict.reason}"

            self.company.record(task.work_unit_id, "execution", "completed" if success else "failed", detail, evidence)
            state = "completed" if success else "failed"
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL WHERE id=?", (state, _now(), _now(), task.id))
            return self.company.finish(task.work_unit_id, success=success, reason=detail, evidence=evidence)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.company.record(task.work_unit_id, "execution", "failed", detail, (f"handler:{task.handler}",))
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE tasks SET state=?,updated_at=?,heartbeat_at=?,lease_until=NULL WHERE id=?", ("failed", _now(), _now(), task.id))
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
        """Execute StateGraph or GraphAgentTeam under the durable supervisor boundary."""
        from .state_graph import StateGraph, GraphAgentTeam
        if isinstance(graph, GraphAgentTeam):
            return graph.run(task_id, intent, context)
        if isinstance(graph, StateGraph):
            return graph.run(task_id, intent, context)
        # Backward compatibility for existing Graph callers.
        from .orchestration import Orchestrator
        return Orchestrator(graph).run(task_id, intent, context)

__all__ = ["ExecutionController", "DurableTask", "Handler"]