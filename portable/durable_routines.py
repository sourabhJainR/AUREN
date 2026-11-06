"""Durable trigger registry for AUREN routines.

This module owns routine definitions and trigger receipts only. It deliberately
does not schedule processes, queue tasks, own approvals, or execute handlers.
Every occurrence is submitted through the canonical ExecutionController adapter.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    routine_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    spec_digest TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT
);
CREATE TABLE IF NOT EXISTS routine_occurrences (
    occurrence_key TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL,
    routine_version INTEGER NOT NULL,
    trigger_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    work_key TEXT NOT NULL UNIQUE,
    task_id TEXT,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(routine_id) REFERENCES routines(routine_id)
);
CREATE INDEX IF NOT EXISTS idx_routine_occurrences_routine
ON routine_occurrences(routine_id, created_at);
"""


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoutineSpec:
    routine_id: str
    version: int
    handler: str
    payload: Mapping[str, Any]
    trigger: str
    timezone: str = "UTC"
    run_at: str | None = None
    interval_seconds: int | None = None
    event_type: str | None = None
    goal: str | None = None
    deadline_seconds: int | None = None
    approval_required: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        for field_name in ("routine_id", "handler"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{field_name} must be non-empty bounded text")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if self.trigger not in {"manual", "once", "interval", "webhook"}:
            raise ValueError("unsupported routine trigger")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        try:
            encoded = json.dumps(dict(self.payload), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("routine payload must be finite JSON data") from exc
        if len(encoded.encode("utf-8")) > 64_000:
            raise ValueError("routine payload exceeds 64 KB")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError("unknown routine timezone") from exc
        if self.trigger == "once":
            if not self.run_at:
                raise ValueError("once trigger requires run_at")
            parsed = datetime.fromisoformat(self.run_at)
            _utc(parsed)
        elif self.run_at is not None:
            raise ValueError("run_at is only valid for once triggers")
        if self.trigger == "interval":
            if type(self.interval_seconds) is not int or not 60 <= self.interval_seconds <= 31_536_000:
                raise ValueError("interval_seconds must be between 60 and 31536000")
        elif self.interval_seconds is not None:
            raise ValueError("interval_seconds is only valid for interval triggers")
        if self.trigger == "webhook":
            if not isinstance(self.event_type, str) or not self.event_type.strip():
                raise ValueError("webhook trigger requires event_type")
        elif self.event_type is not None:
            raise ValueError("event_type is only valid for webhook triggers")
        if self.deadline_seconds is not None and (
            type(self.deadline_seconds) is not int or not 1 <= self.deadline_seconds <= 86400
        ):
            raise ValueError("deadline_seconds must be between 1 and 86400")
        if type(self.approval_required) is not bool or type(self.enabled) is not bool:
            raise ValueError("approval_required and enabled must be booleans")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class RoutineDispatch:
    occurrence_key: str
    work_key: str
    status: str
    task_id: str | None = None
    reason: str | None = None


class DurableRoutineRegistry:
    """Idempotent trigger receipts around the existing canonical task controller.

    submit must call ExecutionController.submit(handler, payload, goal=...,
    work_key=..., deadline_seconds=...). reconcile must perform an authoritative
    lookup by the same work_key. Without reconciliation, an uncertain submission
    is never replayed.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def register(self, spec: RoutineSpec) -> str:
        if not isinstance(spec, RoutineSpec):
            raise TypeError("spec must be a RoutineSpec")
        payload = asdict(spec)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if spec.trigger == "once":
            initial = _iso(_utc(datetime.fromisoformat(spec.run_at or "")))
        elif spec.trigger == "interval":
            initial = _iso(datetime.now(timezone.utc) + timedelta(seconds=spec.interval_seconds or 0))
        else:
            initial = None
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT version,spec_digest FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()
            if current:
                if spec.version < current["version"]:
                    raise ValueError("routine version cannot move backwards")
                if spec.version == current["version"]:
                    if spec.digest != current["spec_digest"]:
                        raise ValueError("same routine version has different content")
                    db.execute("COMMIT")
                    return spec.digest
            db.execute(
                """INSERT INTO routines(routine_id,version,spec_json,spec_digest,enabled,next_run_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(routine_id) DO UPDATE SET
                   version=excluded.version,spec_json=excluded.spec_json,
                   spec_digest=excluded.spec_digest,enabled=excluded.enabled,
                   next_run_at=excluded.next_run_at""",
                (spec.routine_id, spec.version, encoded, spec.digest, int(spec.enabled), initial),
            )
            db.execute("COMMIT")
        return spec.digest

    def set_enabled(self, routine_id: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with closing(self._connect()) as db:
            cursor = db.execute(
                "UPDATE routines SET enabled=? WHERE routine_id=?",
                (int(enabled), routine_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(routine_id)

    def get(self, routine_id: str) -> RoutineSpec:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT spec_json,enabled FROM routines WHERE routine_id=?", (routine_id,)
            ).fetchone()
        if row is None:
            raise KeyError(routine_id)
        payload = json.loads(row["spec_json"])
        payload["enabled"] = bool(row["enabled"])
        return RoutineSpec(**payload)

    def dispatch_manual(
        self, routine_id: str, *, submit: Callable[..., Any],
        approval: Callable[[RoutineSpec], bool] | None = None,
        reconcile: Callable[[str], Any | None] | None = None,
    ) -> RoutineDispatch:
        return self._dispatch(
            routine_id, "manual", "manual", submit=submit,
            approval=approval, reconcile=reconcile,
        )

    def dispatch_webhook(
        self, routine_id: str, *, event_id: str, event_type: str,
        payload: Mapping[str, Any], authenticate: Callable[[str, str, Mapping[str, Any]], bool],
        submit: Callable[..., Any], approval: Callable[[RoutineSpec], bool] | None = None,
        reconcile: Callable[[str], Any | None] | None = None,
    ) -> RoutineDispatch:
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            raise ValueError("event_id must be non-empty bounded text")
        if not callable(authenticate):
            raise TypeError("webhook authentication verifier is required")
        try:
            encoded_event = json.dumps(dict(payload), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("webhook payload must be finite JSON data") from exc
        if len(encoded_event.encode("utf-8")) > 64_000:
            raise ValueError("webhook payload exceeds 64 KB")
        if authenticate(routine_id, event_id, payload) is not True:
            raise PermissionError("webhook authentication failed")
        spec = self.get(routine_id)
        if spec.trigger != "webhook" or spec.event_type != event_type:
            raise ValueError("webhook event does not match the registered routine")
        return self._dispatch(
            routine_id, "webhook", event_id, submit=submit, approval=approval,
            reconcile=reconcile, event_payload=payload,
        )

    def dispatch_due(
        self, *, now: datetime, submit: Callable[..., Any],
        approval: Callable[[RoutineSpec], bool] | None = None,
        reconcile: Callable[[str], Any | None] | None = None,
        limit: int = 32,
    ) -> tuple[RoutineDispatch, ...]:
        instant = _utc(now)
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("limit must be between 1 and 256")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT routine_id,spec_json,next_run_at FROM routines WHERE enabled=1 "
                "AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at,routine_id LIMIT ?",
                (_iso(instant), limit),
            ).fetchall()
        results = []
        for row in rows:
            spec = RoutineSpec(**json.loads(row["spec_json"]))
            scheduled = _utc(datetime.fromisoformat(row["next_run_at"]))
            occurrence = f"{spec.routine_id}:v{spec.version}:{_iso(scheduled)}"
            dispatched = self._dispatch(
                spec.routine_id, spec.trigger, occurrence, submit=submit,
                approval=approval, reconcile=reconcile, scheduled_at=scheduled,
            )
            results.append(dispatched)
            if dispatched.status not in {"submitted", "duplicate"}:
                continue
            if spec.trigger == "interval":
                next_run = scheduled + timedelta(seconds=spec.interval_seconds or 0)
                with closing(self._connect()) as db:
                    db.execute(
                        "UPDATE routines SET next_run_at=? WHERE routine_id=? AND version=? AND next_run_at=?",
                        (_iso(next_run), spec.routine_id, spec.version, row["next_run_at"]),
                    )
            elif spec.trigger == "once":
                with closing(self._connect()) as db:
                    db.execute(
                        "UPDATE routines SET next_run_at=NULL WHERE routine_id=? AND version=?",
                        (spec.routine_id, spec.version),
                    )
        return tuple(results)

    def _dispatch(
        self, routine_id: str, trigger_kind: str, trigger_key: str, *,
        submit: Callable[..., Any], approval: Callable[[RoutineSpec], bool] | None,
        reconcile: Callable[[str], Any | None] | None,
        event_payload: Mapping[str, Any] | None = None,
        scheduled_at: datetime | None = None,
    ) -> RoutineDispatch:
        spec = self.get(routine_id)
        if not spec.enabled:
            return RoutineDispatch("", "", "disabled", reason="routine is disabled")
        if spec.approval_required and (approval is None or approval(spec) is not True):
            return RoutineDispatch("", "", "blocked", reason="required approval is absent")
        occurrence_key = _digest({
            "routine_id": routine_id, "version": spec.version,
            "trigger_kind": trigger_kind, "trigger_key": trigger_key,
        })
        work_key = f"routine:{routine_id}:v{spec.version}:{occurrence_key[:32]}"
        now = _iso(datetime.now(timezone.utc))
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT status,task_id,receipt_json FROM routine_occurrences WHERE occurrence_key=?",
                (occurrence_key,),
            ).fetchone()
            if previous:
                if previous["status"] == "submitted":
                    db.execute("COMMIT")
                    return RoutineDispatch(occurrence_key, work_key, "duplicate", previous["task_id"])
                if previous["status"] in {"dispatching", "unknown"}:
                    db.execute("COMMIT")
                    if reconcile is None:
                        return RoutineDispatch(occurrence_key, work_key, "unknown",
                                               reason="submission outcome requires authoritative reconciliation")
                    try:
                        recovered = reconcile(work_key)
                    except Exception as exc:
                        return RoutineDispatch(occurrence_key, work_key, "unknown",
                                               reason=f"reconciliation failed: {type(exc).__name__}")
                    if recovered is None:
                        return RoutineDispatch(occurrence_key, work_key, "unknown",
                                               reason="no authoritative task lookup result; replay refused")
                    task_id = getattr(recovered, "id", None) or (
                        recovered.get("id") if isinstance(recovered, Mapping) else None
                    )
                    if not isinstance(task_id, str) or not task_id:
                        return RoutineDispatch(occurrence_key, work_key, "unknown",
                                               reason="reconciliation returned no task identity")
                    self._mark_submitted(occurrence_key, task_id, {"reconciled": True})
                    return RoutineDispatch(occurrence_key, work_key, "submitted", task_id)
                db.execute("COMMIT")
                return RoutineDispatch(occurrence_key, work_key, previous["status"])
            receipt = {"state": "dispatching", "routine_digest": spec.digest}
            db.execute(
                """INSERT INTO routine_occurrences
                   (occurrence_key,routine_id,routine_version,trigger_kind,status,work_key,
                    receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (occurrence_key, routine_id, spec.version, trigger_kind, "dispatching",
                 work_key, json.dumps(receipt, sort_keys=True), now, now),
            )
            db.execute("COMMIT")

        payload = dict(spec.payload)
        if event_payload is not None:
            payload["_auren_trigger_event"] = dict(event_payload)
        if scheduled_at is not None:
            payload["_auren_scheduled_at"] = _iso(scheduled_at)
        try:
            task = submit(
                spec.handler, payload, goal=spec.goal, work_key=work_key,
                deadline_seconds=spec.deadline_seconds,
            )
            task_id = getattr(task, "id", None) or (
                task.get("id") if isinstance(task, Mapping) else None
            )
            if not isinstance(task_id, str) or not task_id:
                raise RuntimeError("canonical controller returned no durable task ID")
            self._mark_submitted(occurrence_key, task_id, {"submitted": True})
            return RoutineDispatch(occurrence_key, work_key, "submitted", task_id)
        except Exception as exc:
            self._mark_unknown(occurrence_key, type(exc).__name__)
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason=f"submission outcome is uncertain ({type(exc).__name__}); reconcile before retry",
            )

    def _mark_submitted(self, occurrence_key: str, task_id: str, receipt: Mapping[str, Any]) -> None:
        now = _iso(datetime.now(timezone.utc))
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE routine_occurrences SET status='submitted',task_id=?,receipt_json=?,updated_at=? "
                "WHERE occurrence_key=?",
                (task_id, json.dumps(dict(receipt), sort_keys=True), now, occurrence_key),
            )

    def _mark_unknown(self, occurrence_key: str, error_type: str) -> None:
        now = _iso(datetime.now(timezone.utc))
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE routine_occurrences SET status='unknown',receipt_json=?,updated_at=? "
                "WHERE occurrence_key=?",
                (json.dumps({"state": "unknown", "error_type": error_type}, sort_keys=True),
                 now, occurrence_key),
            )

    def occurrence(self, occurrence_key: str) -> Mapping[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM routine_occurrences WHERE occurrence_key=?", (occurrence_key,)
            ).fetchone()
        return dict(row) if row else None


__all__ = ["DurableRoutineRegistry", "RoutineDispatch", "RoutineSpec"]
