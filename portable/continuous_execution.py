"""Durable continuous execution and exact-head CI polling.

The supervisor is deliberately an orchestration loop, not a new execution
authority. Task execution, CI promotion gates, authorization and evidence remain
owned by ExecutionController. The stop request is durable and auditable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .execution_controller import ExecutionController

_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TERMINAL_CI = {"success", "failure"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _safe_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Bound and redact externally supplied audit data before it is persisted."""
    lowered = key.lower()
    if any(marker in lowered for marker in ("token", "secret", "password", "authorization", "credential", "cookie")):
        return "[REDACTED]"
    if depth >= 5:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, Mapping):
        return {
            str(k)[:120]: _safe_value(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:100]]
    return f"[UNSUPPORTED:{type(value).__name__}]"


class GitHubActionsCheckPoller:
    """Poll commit-bound GitHub check-runs; never treats missing/skipped checks as passes."""

    def __init__(
        self,
        repository: str,
        head_sha: str,
        required_checks: Sequence[str],
        *,
        timeout_seconds: float = 10.0,
        token: str | None = None,
    ) -> None:
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must be owner/name")
        if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
            raise ValueError("head_sha must be a full lowercase commit SHA")
        checks = tuple(dict.fromkeys(required_checks))
        if not checks or any(not isinstance(item, str) or not item.strip() for item in checks):
            raise ValueError("required_checks must contain non-empty exact check names")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60")
        self.repository = repository
        self.head_sha = head_sha
        self.required_checks = checks
        self.timeout_seconds = float(timeout_seconds)
        self.token = token if token is not None else (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))

    def __call__(self) -> Mapping[str, Any]:
        url = f"https://api.github.com/repos/{self.repository}/commits/{self.head_sha}/check-runs?per_page=100"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AUREN-continuous-execution"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Do not copy exception text: URL errors can contain operational details.
            return {"status": "poll_error", "repository": self.repository, "head_sha": self.head_sha, "error_type": type(exc).__name__}
        if len(raw) > _MAX_RESPONSE_BYTES:
            return {"status": "poll_error", "repository": self.repository, "head_sha": self.head_sha, "error_type": "response_too_large"}
        try:
            payload = json.loads(raw.decode("utf-8"))
            runs = payload["check_runs"]
            if not isinstance(runs, list):
                raise ValueError("check_runs is not a list")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return {"status": "poll_error", "repository": self.repository, "head_sha": self.head_sha, "error_type": "invalid_response"}

        by_name: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("name"), str):
                continue
            by_name.setdefault(run["name"], []).append(run)
        details: dict[str, Any] = {}
        missing: list[str] = []
        pending: list[str] = []
        failed: list[str] = []
        for name in self.required_checks:
            matches = by_name.get(name, [])
            if not matches:
                missing.append(name)
                continue
            # A repeated check name is considered passing only when every observed
            # run is complete and successful; this avoids hiding a failed rerun.
            statuses = [{"status": item.get("status"), "conclusion": item.get("conclusion")} for item in matches]
            details[name] = statuses
            if any(item.get("status") != "completed" for item in matches):
                pending.append(name)
            elif any(item.get("conclusion") != "success" for item in matches):
                # Skipped, neutral, cancelled, timed_out and action_required are not passes.
                failed.append(name)
        if failed:
            status = "failure"
        elif pending:
            status = "running"
        elif missing:
            status = "waiting_for_required_checks"
        else:
            status = "success"
        return {
            "status": status, "repository": self.repository, "head_sha": self.head_sha,
            "required_checks": list(self.required_checks), "missing_checks": missing,
            "pending_checks": pending, "failed_checks": failed, "checks": details,
        }


class ContinuousExecutionSupervisor:
    """Single-owner durable loop over queued tasks and registered external pollers.

    The controller remains authoritative for task claims and side-effect gates.
    A bounded lease prevents two supervisors from concurrently driving the same
    project. Each poll, cycle, stop request and terminal state is hash-chained.
    """

    def __init__(
        self,
        controller: ExecutionController,
        *,
        pollers: Mapping[str, Callable[[], Mapping[str, Any]]] | None = None,
        lease_seconds: int = 30,
    ) -> None:
        if not isinstance(controller, ExecutionController):
            raise TypeError("controller must be an ExecutionController")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 5 and 300")
        self.controller = controller
        self.root = controller.root
        self.path = self.root / ".auren" / "continuous-execution.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pollers = dict(pollers or {})
        for name, callback in self.pollers.items():
            if not isinstance(name, str) or not name.strip() or not callable(callback):
                raise ValueError("pollers require non-empty names and callable values")
        self.lease_seconds = lease_seconds
        self.owner_id = uuid4().hex
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS control(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    state TEXT NOT NULL,
                    owner_id TEXT,
                    lease_until TEXT,
                    cycle_count INTEGER NOT NULL DEFAULT 0,
                    last_cycle_at TEXT,
                    stop_reason TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_digest TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO control(id,state,updated_at) VALUES(1,'stopped',?)",
                (_now(),),
            )

    def _event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        body = _canonical(_safe_value(dict(payload)))
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
            prev_digest = str(previous[0]) if previous else "0" * 64
            created_at = _now()
            material = _canonical({
                "run_id": run_id, "event_type": event_type, "payload_json": body,
                "previous_digest": prev_digest, "created_at": created_at,
            })
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
            db.execute(
                "INSERT INTO events(run_id,event_type,payload_json,previous_digest,digest,created_at) VALUES(?,?,?,?,?,?)",
                (run_id, event_type, body, prev_digest, digest, created_at),
            )

    def request_stop(self, reason: str = "operator_stop_command") -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
            raise ValueError("stop reason must be non-empty text of at most 200 characters")
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM control WHERE id=1").fetchone()
            prior = row[0] if row else "stopped"
            db.execute(
                "UPDATE control SET state='stop_requested',stop_reason=?,updated_at=? WHERE id=1",
                (reason.strip(), _now()),
            )
        self._event("operator", "stop_requested", {"reason": reason.strip(), "prior_state": prior})
        return self.status()

    def status(self, *, event_limit: int = 20) -> dict[str, Any]:
        if type(event_limit) is not int or not 1 <= event_limit <= 100:
            raise ValueError("event_limit must be between 1 and 100")
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT state,owner_id,lease_until,cycle_count,last_cycle_at,stop_reason,updated_at FROM control WHERE id=1"
            ).fetchone()
            counts = {str(state): int(count) for state, count in db.execute(
                "SELECT state,COUNT(*) FROM tasks GROUP BY state"
            ).fetchall()} if self._table_exists(db, "tasks") else {}
            pending_tasks = []
            recent_terminal_tasks = []
            if self._table_exists(db, "tasks"):
                columns = {item[1] for item in db.execute("PRAGMA table_info(tasks)").fetchall()}
                optional = lambda column: column if column in columns else "NULL AS " + column
                query = (
                    "SELECT id,handler,state,attempts,work_unit_id,updated_at,"
                    + optional("next_retry_at") + "," + optional("quota_blocked")
                    + " FROM tasks WHERE state NOT IN ('completed','failed','cancelled') ORDER BY updated_at LIMIT 100"
                )
                pending_tasks = [
                    {"task_id": row[0], "handler": row[1], "state": row[2], "attempts": int(row[3]),
                     "work_unit_id": row[4], "updated_at": row[5], "next_retry_at": row[6], "quota_blocked": row[7]}
                    for row in db.execute(query).fetchall()
                ]
                terminal_query = (
                    "SELECT id,handler,state,attempts,work_unit_id,updated_at FROM tasks "
                    "WHERE state IN ('completed','failed','cancelled') ORDER BY updated_at DESC LIMIT 20"
                )
                recent_terminal_tasks = [
                    {"task_id": row[0], "handler": row[1], "state": row[2], "attempts": int(row[3]),
                     "work_unit_id": row[4], "updated_at": row[5]}
                    for row in db.execute(terminal_query).fetchall()
                ]
            events = db.execute(
                "SELECT sequence,event_type,payload_json,digest,created_at FROM events ORDER BY sequence DESC LIMIT ?",
                (event_limit,),
            ).fetchall()
        return {
            "state": row[0], "owner_id": row[1], "lease_until": row[2], "cycle_count": int(row[3]),
            "last_cycle_at": row[4], "stop_reason": row[5], "updated_at": row[6],
            "task_counts": counts, "pending_tasks": pending_tasks,
            "recent_terminal_tasks": recent_terminal_tasks, "pollers": sorted(self.pollers),
            "recent_events": [
                {"sequence": int(e[0]), "event_type": e[1], "payload": json.loads(e[2]), "digest": e[3], "created_at": e[4]}
                for e in events
            ],
        }

    @staticmethod
    def _table_exists(db: sqlite3.Connection, name: str) -> bool:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def verify_audit_chain(self) -> bool:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT run_id,event_type,payload_json,previous_digest,digest,created_at FROM events ORDER BY sequence"
            ).fetchall()
        previous = "0" * 64
        for run_id, event_type, payload_json, prev_digest, digest, created_at in rows:
            if prev_digest != previous:
                return False
            material = _canonical({
                "run_id": run_id, "event_type": event_type, "payload_json": payload_json,
                "previous_digest": prev_digest, "created_at": created_at,
            })
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if expected != digest:
                return False
            previous = digest
        return True

    def _should_stop(self) -> tuple[bool, str | None]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT state,stop_reason FROM control WHERE id=1").fetchone()
        return (row[0] == "stop_requested", row[1])

    def _claim(self, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            state, owner, lease = db.execute(
                "SELECT state,owner_id,lease_until FROM control WHERE id=1"
            ).fetchone()
            if state == "stop_requested" and owner and lease:
                try:
                    lease_time = datetime.fromisoformat(lease)
                except ValueError:
                    lease_time = datetime.min.replace(tzinfo=timezone.utc)
                if lease_time.tzinfo is not None and lease_time > now and owner != self.owner_id:
                    raise RuntimeError("stop is being honored by the live AUREN supervisor")
            if state == "running" and owner and lease:
                try:
                    lease_time = datetime.fromisoformat(lease)
                except ValueError:
                    lease_time = datetime.min.replace(tzinfo=timezone.utc)
                if lease_time.tzinfo is not None and lease_time > now and owner != self.owner_id:
                    raise RuntimeError("another AUREN continuous supervisor holds the live lease")
            db.execute(
                "UPDATE control SET state='running',owner_id=?,lease_until=?,stop_reason=NULL,updated_at=? WHERE id=1",
                (self.owner_id, lease_until, _now()),
            )
        self._event(run_id, "supervisor_started", {"owner_id": self.owner_id, "lease_until": lease_until})

    def _heartbeat(self, run_id: str, cycle: int) -> None:
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,owner_id FROM control WHERE id=1").fetchone()
            if row[0] == "stop_requested":
                return
            if row[1] != self.owner_id:
                raise RuntimeError("continuous supervisor lease was lost")
            db.execute(
                "UPDATE control SET lease_until=?,cycle_count=?,last_cycle_at=?,updated_at=? WHERE id=1",
                (lease_until, cycle, _now(), _now()),
            )

    def run(
        self,
        *,
        poll_seconds: float = 5.0,
        batch_size: int = 20,
        max_cycles: int | None = None,
        stop_event: Any = None,
    ) -> dict[str, Any]:
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or not 0.1 <= poll_seconds <= 300:
            raise ValueError("poll_seconds must be between 0.1 and 300")
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
            raise ValueError("max_cycles must be None or a positive integer")
        run_id = uuid4().hex
        self._claim(run_id)
        cycles = 0
        terminal = False
        try:
            self._event(run_id, "execution_started", {"poll_seconds": poll_seconds, "batch_size": batch_size, "pollers": sorted(self.pollers)})
            while max_cycles is None or cycles < max_cycles:
                if stop_event is not None and stop_event.is_set():
                    self.request_stop("local_interrupt")
                should_stop, reason = self._should_stop()
                if should_stop:
                    terminal = True
                    break
                cycles += 1
                self._heartbeat(run_id, cycles)
                self._event(run_id, "cycle_started", {"cycle": cycles})
                batch_error = None
                try:
                    self.controller.resume_pending(limit=batch_size, continue_after_durable_failure=True)
                except Exception as exc:
                    # Do not silently turn lease loss or inconsistent durable state into success.
                    batch_error = type(exc).__name__
                    self._event(run_id, "task_batch_error", {"cycle": cycles, "error_type": batch_error})
                    if type(exc).__name__ in {"LeaseLostError", "StatePathConflict"}:
                        raise
                poll_results: dict[str, Any] = {}
                for name, poller in self.pollers.items():
                    try:
                        result = poller()
                        if not isinstance(result, Mapping):
                            raise TypeError("poller must return a mapping")
                        clean = dict(result)
                        if clean.get("status") not in {"success", "failure", "running", "waiting_for_required_checks", "poll_error", "pending"}:
                            raise ValueError("poller returned an unsupported status")
                        poll_results[name] = clean
                        self._event(run_id, "poll_result", {"cycle": cycles, "poller": name, "result": clean})
                    except Exception as exc:
                        poll_results[name] = {"status": "poll_error", "error_type": type(exc).__name__}
                        self._event(run_id, "poll_error", {"cycle": cycles, "poller": name, "error_type": type(exc).__name__})
                task_counts = self.status(event_limit=1)["task_counts"]
                pending_count = sum(count for state, count in task_counts.items() if state not in {"completed", "failed", "cancelled"})
                self._event(run_id, "cycle_completed", {
                    "cycle": cycles, "pending_tasks": pending_count, "task_counts": task_counts,
                    "pollers": {name: value.get("status") for name, value in poll_results.items()},
                    "batch_error": batch_error,
                })
                if max_cycles is not None and cycles >= max_cycles:
                    break
                if stop_event is not None:
                    stop_event.wait(poll_seconds)
                else:
                    time.sleep(poll_seconds)
            state, stop_reason = self._should_stop()
            if state:
                with sqlite3.connect(self.path, timeout=5) as db:
                    db.execute(
                        "UPDATE control SET state='stopped',owner_id=NULL,lease_until=NULL,updated_at=? WHERE id=1",
                        (_now(),),
                    )
                self._event(run_id, "supervisor_stopped", {"reason": stop_reason or "stop_requested", "cycles": cycles})
            elif max_cycles is not None and cycles >= max_cycles:
                with sqlite3.connect(self.path, timeout=5) as db:
                    db.execute(
                        "UPDATE control SET state='stopped',owner_id=NULL,lease_until=NULL,updated_at=? WHERE id=1",
                        (_now(),),
                    )
                self._event(run_id, "supervisor_stopped", {"reason": "cycle_budget_exhausted", "cycles": cycles})
            return {**self.status(), "run_id": run_id, "cycles_this_run": cycles, "audit_chain_valid": self.verify_audit_chain()}
        except Exception as exc:
            with sqlite3.connect(self.path, timeout=5) as db:
                db.execute(
                    "UPDATE control SET state='failed',owner_id=NULL,lease_until=NULL,updated_at=? WHERE id=1 AND owner_id=?",
                    (_now(), self.owner_id),
                )
            self._event(run_id, "supervisor_failed", {"error_type": type(exc).__name__})
            raise


__all__ = ["ContinuousExecutionSupervisor", "GitHubActionsCheckPoller"]
