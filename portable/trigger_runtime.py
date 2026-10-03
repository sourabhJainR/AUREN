"""Durable event triggers that hand work to the existing AUREN orchestrator.

This is a coordination boundary, not an execution engine. It provides
idempotent event intake, single-owner claims, bounded retry/backoff, lease-based
crash recovery, kind isolation, priority ordering and durable status metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Callable, Mapping
import uuid

from .agent_capabilities import AutomationScheduler


_UTC = timezone.utc
_MAX_DETAIL_LENGTH = 2000
_MAX_OUTCOME_LENGTH = 4000


def _utc() -> datetime:
    return datetime.now(_UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trigger times must be timezone-aware")
    return value.astimezone(_UTC)


def _bounded_detail(value: str) -> str:
    return str(value)[:_MAX_DETAIL_LENGTH]


def _bounded_outcome(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("trigger outcome must be a mapping")
    try:
        clean = json.loads(json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise TypeError("trigger outcome must be JSON-compatible") from exc
    encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= _MAX_OUTCOME_LENGTH:
        return clean
    preview = encoded[: _MAX_OUTCOME_LENGTH - 64]
    return {"truncated": True, "preview": preview}


@dataclass(frozen=True)
class TriggerEvent:
    event_id: str
    kind: str
    payload: Mapping[str, Any]
    created_at: str
    available_at: str
    attempts: int
    max_attempts: int
    status: str
    last_detail: str = ""
    priority: int = 1
    claimed_at: str | None = None
    lease_until: str | None = None
    completed_at: str | None = None
    outcome: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerClaim:
    event: TriggerEvent
    claim_id: str


@dataclass(frozen=True)
class TriggerStatus:
    event_id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    priority: int
    available_at: str
    claimed_at: str | None
    lease_until: str | None
    completed_at: str | None
    last_detail: str
    outcome: Mapping[str, Any] = field(default_factory=dict)


class TriggerRuntime:
    """Persisted event inbox with at-most-one active claim per event."""

    def __init__(
        self,
        scheduler: AutomationScheduler,
        *,
        retry_delay_seconds: int = 5,
        max_events: int = 100_000,
        claim_lease_seconds: int = 300,
    ) -> None:
        if not isinstance(scheduler, AutomationScheduler):
            raise TypeError("scheduler must be an AutomationScheduler")
        if retry_delay_seconds < 1 or max_events < 1 or claim_lease_seconds < 1:
            raise ValueError("retry delay, event bound and claim lease must be positive")
        self.scheduler = scheduler
        self.retry_delay_seconds = retry_delay_seconds
        self.max_events = max_events
        self.claim_lease_seconds = claim_lease_seconds
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.scheduler.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS trigger_events(
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    claim_id TEXT,
                    last_detail TEXT NOT NULL DEFAULT '',
                    claimed_at TEXT,
                    lease_until TEXT,
                    completed_at TEXT,
                    priority INTEGER NOT NULL DEFAULT 1,
                    outcome TEXT NOT NULL DEFAULT '{}')"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(trigger_events)")}
            migrations = {
                "last_detail": "ALTER TABLE trigger_events ADD COLUMN last_detail TEXT NOT NULL DEFAULT ''",
                "claimed_at": "ALTER TABLE trigger_events ADD COLUMN claimed_at TEXT",
                "lease_until": "ALTER TABLE trigger_events ADD COLUMN lease_until TEXT",
                "completed_at": "ALTER TABLE trigger_events ADD COLUMN completed_at TEXT",
                "priority": "ALTER TABLE trigger_events ADD COLUMN priority INTEGER NOT NULL DEFAULT 1",
                "outcome": "ALTER TABLE trigger_events ADD COLUMN outcome TEXT NOT NULL DEFAULT '{}'",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    db.execute(statement)
            db.execute("CREATE INDEX IF NOT EXISTS idx_trigger_due ON trigger_events(status, priority, available_at, event_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_trigger_kind_due ON trigger_events(kind, status, priority, available_at, event_id)")

    @staticmethod
    def _select_sql() -> str:
        return (
            "SELECT event_id,kind,payload,created_at,available_at,attempts,max_attempts,status,"
            "last_detail,priority,claimed_at,lease_until,completed_at,outcome FROM trigger_events"
        )

    def emit(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        event_id: str | None = None,
        max_attempts: int = 3,
        not_before: datetime | None = None,
        priority: int = 1,
    ) -> TriggerEvent:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("event kind is required")
        if not isinstance(payload, Mapping):
            raise TypeError("event payload must be a mapping")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if priority not in {0, 1, 2}:
            raise ValueError("priority must be one of: 0, 1, 2")
        try:
            clean_payload = json.loads(json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        except (TypeError, ValueError) as exc:
            raise TypeError("event payload must be JSON-compatible") from exc
        event_id = event_id or uuid.uuid4().hex
        now = _utc()
        available = _aware(not_before) if not_before is not None else now
        payload_json = json.dumps(clean_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._select_sql() + " WHERE event_id=?", (event_id,)).fetchone()
            if row:
                existing = self._event(row)
                if (
                    existing.kind != kind.strip()
                    or json.dumps(existing.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) != payload_json
                    or existing.priority != priority
                ):
                    raise ValueError("event_id already exists with different content")
                return existing
            count = db.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0]
            if count >= self.max_events:
                raise ValueError("trigger event budget exceeded")
            db.execute(
                "INSERT INTO trigger_events(event_id,kind,payload,created_at,available_at,attempts,max_attempts,status,claim_id,last_detail,claimed_at,lease_until,completed_at,priority,outcome) VALUES(?,?,?,?,?,?,?,?,NULL,'',NULL,NULL,NULL,?,?)",
                (event_id, kind.strip(), payload_json, now.isoformat(), available.isoformat(), 0, max_attempts, "pending", priority, "{}"),
            )
        return TriggerEvent(event_id, kind.strip(), clean_payload, now.isoformat(), available.isoformat(), 0, max_attempts, "pending", priority=priority)

    @staticmethod
    def _event(row: tuple[Any, ...]) -> TriggerEvent:
        try:
            outcome = json.loads(row[13] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            outcome = {"invalid_persisted_outcome": True}
        if not isinstance(outcome, Mapping):
            outcome = {}
        return TriggerEvent(
            row[0], row[1], json.loads(row[2]), row[3], row[4], int(row[5]), int(row[6]), row[7], row[8] or "",
            int(row[9]), row[10], row[11], row[12], dict(outcome),
        )

    def due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
        kind: str | None = None,
    ) -> tuple[TriggerEvent, ...]:
        if limit < 1:
            return ()
        now = _aware(now) if now is not None else _utc()
        with self._connect() as db:
            if kind is None:
                rows = db.execute(
                    self._select_sql() + " WHERE status='pending' AND available_at<=? ORDER BY priority,available_at,event_id LIMIT ?",
                    (now.isoformat(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    self._select_sql()
                    + " WHERE kind=? AND ((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until IS NOT NULL AND lease_until<=?)) ORDER BY priority,available_at,event_id LIMIT ?",
                    (kind, now.isoformat(), now.isoformat(), limit),
                ).fetchall()
        return tuple(self._event(row) for row in rows)

    def claim(self, event_id: str, *, now: datetime | None = None) -> TriggerClaim | None:
        now = _aware(now) if now is not None else _utc()
        claim_id = uuid.uuid4().hex
        lease_until = now + timedelta(seconds=self.claim_lease_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._select_sql() + " WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                return None
            status = row[7]
            attempts = int(row[5])
            max_attempts = int(row[6])
            if status == "claimed":
                current_lease = datetime.fromisoformat(row[11]) if row[11] else None
                if current_lease is None or current_lease > now or attempts >= max_attempts:
                    return None
                db.execute(
                    "UPDATE trigger_events SET status='pending',claim_id=NULL,claimed_at=NULL,lease_until=NULL WHERE event_id=? AND status='claimed' AND claim_id IS NOT NULL AND lease_until<=?",
                    (event_id, now.isoformat()),
                )
                status = "pending"
                row = db.execute(self._select_sql() + " WHERE event_id=?", (event_id,)).fetchone()
                attempts = int(row[5])
            if status != "pending" or datetime.fromisoformat(row[4]) > now or attempts >= max_attempts:
                return None
            updated = db.execute(
                "UPDATE trigger_events SET status='claimed',claim_id=?,claimed_at=?,lease_until=?,attempts=attempts+1 WHERE event_id=? AND status='pending' AND claim_id IS NULL",
                (claim_id, now.isoformat(), lease_until.isoformat(), event_id),
            ).rowcount
            if updated != 1:
                return None
            claimed = db.execute(self._select_sql() + " WHERE event_id=?", (event_id,)).fetchone()
        return TriggerClaim(self._event(claimed), claim_id)

    def get(self, event_id: str) -> TriggerStatus | None:
        with self._connect() as db:
            row = db.execute(self._select_sql() + " WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        event = self._event(row)
        return TriggerStatus(
            event_id=event.event_id,
            kind=event.kind,
            status=event.status,
            attempts=event.attempts,
            max_attempts=event.max_attempts,
            priority=event.priority,
            available_at=event.available_at,
            claimed_at=event.claimed_at,
            lease_until=event.lease_until,
            completed_at=event.completed_at,
            last_detail=event.last_detail,
            outcome=dict(event.outcome),
        )

    def complete(
        self,
        event_id: str,
        claim_id: str,
        status: str,
        detail: str = "",
        *,
        now: datetime | None = None,
        outcome: Mapping[str, Any] | None = None,
    ) -> None:
        if status not in {"success", "retryable", "failed", "cancelled"}:
            raise ValueError("invalid trigger completion status")
        now = _aware(now) if now is not None else _utc()
        clean_outcome = _bounded_outcome(outcome)
        outcome_json = json.dumps(clean_outcome, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT attempts,max_attempts,status,claim_id FROM trigger_events WHERE event_id=?", (event_id,)).fetchone()
            if not row or row[2] != "claimed" or row[3] != claim_id:
                raise KeyError("invalid trigger claim")
            attempts, max_attempts = int(row[0]), int(row[1])
            if status == "retryable" and attempts < max_attempts:
                available = now + timedelta(seconds=self.retry_delay_seconds * (2 ** max(0, attempts - 1)))
                db.execute(
                    "UPDATE trigger_events SET status='pending',claim_id=NULL,claimed_at=NULL,lease_until=NULL,completed_at=NULL,available_at=?,last_detail=?,outcome=? WHERE event_id=?",
                    (available.isoformat(), _bounded_detail(detail), outcome_json, event_id),
                )
            else:
                terminal = "success" if status == "success" else "cancelled" if status == "cancelled" else "failed"
                db.execute(
                    "UPDATE trigger_events SET status=?,claim_id=NULL,claimed_at=NULL,lease_until=NULL,available_at=?,completed_at=?,last_detail=?,outcome=? WHERE event_id=?",
                    (terminal, now.isoformat(), now.isoformat(), _bounded_detail(detail), outcome_json, event_id),
                )

    def dispatch_due(
        self,
        handler: Callable[[TriggerEvent], Any],
        *,
        now: datetime | None = None,
        limit: int = 20,
        kind: str | None = None,
    ) -> list[Any]:
        if not callable(handler):
            raise TypeError("handler must be callable")
        results: list[Any] = []
        effective_now = _aware(now) if now is not None else _utc()
        for event in self.due(now=effective_now, limit=limit, kind=kind):
            claim = self.claim(event.event_id, now=effective_now)
            if claim is None:
                continue
            try:
                result = handler(claim.event)
            except Exception as exc:
                self.complete(claim.event.event_id, claim.claim_id, "retryable", str(exc), now=effective_now, outcome={"error_type": type(exc).__name__})
                continue
            self.complete(claim.event.event_id, claim.claim_id, "success", now=effective_now)
            results.append(result)
        return results


__all__ = ["TriggerClaim", "TriggerEvent", "TriggerRuntime", "TriggerStatus"]
