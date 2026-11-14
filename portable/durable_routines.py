"""Durable trigger registry for AUREN routines.

This module owns routine definitions and trigger receipts only. It deliberately
does not schedule processes, queue tasks, decide approvals, or execute handlers.
Every occurrence is submitted through the canonical ExecutionController adapter.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
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


def verify_hmac_webhook_signature(
    secret: bytes | str,
    timestamp: str | int,
    signature: str,
    *,
    routine_id: str,
    event_id: str,
    payload: Mapping[str, Any],
    now: datetime | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a timestamped HMAC-SHA256 webhook envelope without storing secrets.

    The signed bytes bind the timestamp, routine identity, event identity and
    canonical JSON payload. The registry separately persists event IDs to reject
    redelivery. Provider-specific header parsing remains the host's responsibility.
    """
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        secret_bytes = secret
    else:
        return False
    if not secret_bytes or not isinstance(signature, str) or type(timestamp) is bool:
        return False
    if (
        not isinstance(routine_id, str) or not routine_id.strip()
        or not isinstance(event_id, str) or not event_id.strip()
        or type(tolerance_seconds) is not int or not 1 <= tolerance_seconds <= 3600
    ):
        return False
    try:
        timestamp_value = int(timestamp)
        canonical_payload = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        current_time = _utc(now).timestamp()
        within_window = abs(current_time - timestamp_value) <= tolerance_seconds
    except (TypeError, ValueError, OverflowError):
        return False
    if not within_window:
        return False
    signed = "\n".join((
        str(timestamp_value), routine_id, event_id, canonical_payload,
    )).encode("utf-8")
    expected = hmac.new(secret_bytes, signed, hashlib.sha256).hexdigest()
    supplied = signature.strip()
    if supplied.lower().startswith("sha256="):
        supplied = supplied[7:]
    return hmac.compare_digest(expected, supplied.lower())


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
    profile_digest: str | None = None
    policy_digest: str | None = None
    concurrency_policy: str = "allow"
    enabled: bool = False

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
        if not isinstance(self.timezone, str) or not self.timezone.strip():
            raise ValueError("unknown routine timezone")
        # Windows Python does not ship an IANA database. UTC is fixed and can
        # be validated with the standard library without an external tzdata package.
        if self.timezone not in {"UTC", "Etc/UTC", "GMT", "Etc/GMT"}:
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
        if not isinstance(self.concurrency_policy, str) or self.concurrency_policy not in {"allow", "skip"}:
            raise ValueError("concurrency_policy must be allow or skip")
        for field_name in ("profile_digest", "policy_digest"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")

    @property
    def digest(self) -> str:
        payload = asdict(self)
        payload.pop("enabled", None)
        return _digest(payload)


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
                "SELECT version,spec_digest,spec_json FROM routines WHERE routine_id=?",
                (spec.routine_id,),
            ).fetchone()
            if current:
                if spec.version < current["version"]:
                    raise ValueError("routine version cannot move backwards")
                if spec.version == current["version"]:
                    # New optional fields are defaulted when older persisted specs
                    # are loaded. Compare normalized definitions so an upgrade
                    # does not make an unchanged legacy routine look mutated.
                    stored_payload = json.loads(current["spec_json"])
                    if not isinstance(stored_payload, dict):
                        raise ValueError("persisted routine definition is corrupt")
                    stored_digest_payload = dict(stored_payload)
                    stored_digest_payload.pop("enabled", None)
                    if current["spec_digest"] != _digest(stored_digest_payload):
                        raise ValueError("persisted routine definition digest mismatch")
                    requested_payload = dict(payload)
                    for field_name, default in (
                        ("profile_digest", None),
                        ("policy_digest", None),
                        ("concurrency_policy", "allow"),
                    ):
                        stored_payload.setdefault(field_name, default)
                    stored_payload.pop("enabled", None)
                    requested_payload.pop("enabled", None)
                    if stored_payload != requested_payload:
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
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE routines SET enabled=? WHERE routine_id=?",
                (int(enabled), routine_id),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise KeyError(routine_id)
            db.execute("COMMIT")

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
        self, routine_id: str, *, invocation_id: str, submit: Callable[..., Any],
        approval: Callable[[RoutineSpec], Any] | None = None,
        reconcile: Callable[[str], Any | None] | None = None,
    ) -> RoutineDispatch:
        if not isinstance(invocation_id, str) or not invocation_id.strip() or len(invocation_id) > 256:
            raise ValueError("invocation_id must be non-empty bounded text")
        return self._dispatch(
            routine_id, "manual", invocation_id, submit=submit,
            approval=approval, reconcile=reconcile,
        )

    def dispatch_webhook(
        self, routine_id: str, *, event_id: str, event_type: str,
        payload: Mapping[str, Any], authenticate: Callable[[str, str, Mapping[str, Any]], bool],
        submit: Callable[..., Any], approval: Callable[[RoutineSpec], Any] | None = None,
        reconcile: Callable[[str], Any | None] | None = None,
    ) -> RoutineDispatch:
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            raise ValueError("event_id must be non-empty bounded text")
        if not callable(authenticate):
            raise TypeError("webhook authentication verifier is required")
        try:
            encoded_event = json.dumps(
                dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            # Keep a detached canonical snapshot for the receipt and task payload.
            # The verifier receives a separate copy so it cannot mutate what is
            # authenticated and later dispatched.
            clean_payload = json.loads(encoded_event)
        except (TypeError, ValueError) as exc:
            raise ValueError("webhook payload must be finite JSON data") from exc
        if len(encoded_event.encode("utf-8")) > 64_000:
            raise ValueError("webhook payload exceeds 64 KB")
        if authenticate(routine_id, event_id, json.loads(encoded_event)) is not True:
            raise PermissionError("webhook authentication failed")
        spec = self.get(routine_id)
        if spec.trigger != "webhook" or spec.event_type != event_type:
            raise ValueError("webhook event does not match the registered routine")
        return self._dispatch(
            routine_id, "webhook", event_id, submit=submit, approval=approval,
            reconcile=reconcile, event_payload=clean_payload, expected_version=spec.version,
        )

    def dispatch_due(
        self, *, now: datetime, submit: Callable[..., Any],
        approval: Callable[[RoutineSpec], Any] | None = None,
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
            schedule_cursor = _utc(datetime.fromisoformat(row["next_run_at"]))
            scheduled = schedule_cursor
            missed_intervals = 0
            if spec.trigger == "interval":
                interval = spec.interval_seconds or 0
                existing = self._scheduled_occurrence(
                    spec.routine_id, spec.version, schedule_cursor,
                )
                if existing is not None:
                    if existing["status"] == "blocked_approval":
                        existing_receipt = json.loads(existing["receipt_json"])
                        existing_scheduled = _utc(datetime.fromisoformat(
                            existing_receipt.get("scheduled_at") or _iso(schedule_cursor)
                        ))
                        missed_intervals = max(
                            0, int((instant - schedule_cursor).total_seconds() // interval)
                        )
                        trigger_key = f"{spec.routine_id}:v{spec.version}:{_iso(existing_scheduled)}"
                        dispatched = self._dispatch(
                            spec.routine_id, spec.trigger, trigger_key, submit=submit,
                            approval=approval, reconcile=reconcile,
                            scheduled_at=existing_scheduled, schedule_cursor=schedule_cursor,
                            expected_version=spec.version, missed_intervals=missed_intervals,
                        )
                    else:
                        dispatched = self._recover_scheduled_occurrence(
                            existing, now=instant, interval_seconds=interval,
                            reconcile=reconcile,
                        )
                    results.append(dispatched)
                    if dispatched.status in {"submitted", "duplicate"}:
                        next_run = self._next_interval_after(
                            schedule_cursor, instant, interval,
                        )
                        with closing(self._connect()) as db:
                            db.execute(
                                "UPDATE routines SET next_run_at=? WHERE routine_id=? AND version=? AND next_run_at=?",
                                (_iso(next_run), spec.routine_id, spec.version, row["next_run_at"]),
                            )
                    continue
                missed_intervals = max(
                    0, int((instant - schedule_cursor).total_seconds() // interval)
                )
                # Coalesce downtime into one occurrence. Do not replay every missed
                # interval after an outage or let a stale schedule create a backlog.
                scheduled = schedule_cursor + timedelta(seconds=missed_intervals * interval)
            occurrence = f"{spec.routine_id}:v{spec.version}:{_iso(scheduled)}"
            dispatched = self._dispatch(
                spec.routine_id, spec.trigger, occurrence, submit=submit,
                approval=approval, reconcile=reconcile, scheduled_at=scheduled,
                schedule_cursor=schedule_cursor if spec.trigger == "interval" else None,
                expected_version=spec.version, missed_intervals=missed_intervals,
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

    def _scheduled_occurrence(
        self, routine_id: str, version: int, schedule_cursor: datetime,
    ) -> dict[str, Any] | None:
        """Find an occurrence already claimed for the current durable schedule cursor."""
        cursor = _iso(schedule_cursor)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT occurrence_key,work_key,status,task_id,receipt_json "
                "FROM routine_occurrences WHERE routine_id=? AND routine_version=? "
                "AND trigger_kind='interval' ORDER BY created_at DESC LIMIT 1",
                (routine_id, version),
            ).fetchall()
        for row in rows:
            receipt = json.loads(row["receipt_json"])
            if receipt.get("schedule_cursor") == cursor:
                return dict(row)
        return None

    @staticmethod
    def _next_interval_after(cursor: datetime, now: datetime, interval_seconds: int) -> datetime:
        elapsed = max(0, int((now - cursor).total_seconds() // interval_seconds))
        return cursor + timedelta(seconds=(elapsed + 1) * interval_seconds)

    def _recover_scheduled_occurrence(
        self, occurrence: Mapping[str, Any], *, now: datetime,
        interval_seconds: int, reconcile: Callable[[str], Any | None] | None,
    ) -> RoutineDispatch:
        """Reconcile an existing interval occurrence before any later slot is admitted."""
        occurrence_key = str(occurrence["occurrence_key"])
        work_key = str(occurrence["work_key"])
        status = str(occurrence["status"])
        if status == "submitted":
            receipt = json.loads(occurrence["receipt_json"])
            coalesced = receipt.get("coalesced_missed_intervals", 0)
            scheduled_at = receipt.get("scheduled_at")
            if isinstance(scheduled_at, str):
                try:
                    due_at = _utc(datetime.fromisoformat(scheduled_at))
                    coalesced += max(
                        0, int((now - due_at).total_seconds() // interval_seconds)
                    )
                except (TypeError, ValueError):
                    pass
            if coalesced != receipt.get("coalesced_missed_intervals", 0):
                self._update_receipt(
                    occurrence_key, status="submitted",
                    updates={"coalesced_missed_intervals": coalesced,
                             "coalesced_after_submission": True},
                )
            return RoutineDispatch(occurrence_key, work_key, "duplicate", occurrence.get("task_id"))
        if status == "cancellation_requested":
            if reconcile is None:
                return RoutineDispatch(
                    occurrence_key, work_key, "cancellation_requested", occurrence.get("task_id"),
                    "cancellation outcome requires authoritative task-state reconciliation",
                )
            try:
                recovered = reconcile(work_key)
            except Exception as exc:
                return RoutineDispatch(
                    occurrence_key, work_key, "unknown", occurrence.get("task_id"),
                    f"cancellation reconciliation failed ({type(exc).__name__})",
                )
            task_state = (
                recovered.get("state") if isinstance(recovered, Mapping)
                else getattr(recovered, "state", None)
            ) if recovered is not None else None
            if task_state in {"completed", "failed", "cancelled"}:
                self._update_receipt(
                    occurrence_key, status=task_state,
                    updates={"state": task_state, "terminal_reconciled": True},
                )
                return RoutineDispatch(occurrence_key, work_key, "duplicate", occurrence.get("task_id"))
            return RoutineDispatch(
                occurrence_key, work_key, "cancellation_requested", occurrence.get("task_id"),
                "task remains nonterminal after cancellation request",
            )
        if status not in {"dispatching", "unknown"}:
            return RoutineDispatch(occurrence_key, work_key, status)
        if reconcile is None:
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason="scheduled submission outcome requires authoritative reconciliation; replay refused",
            )
        try:
            recovered = reconcile(work_key)
        except Exception as exc:
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason=f"reconciliation failed: {type(exc).__name__}",
            )
        if recovered is None:
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason="no authoritative task lookup result; replay refused",
            )
        task_id = getattr(recovered, "id", None) or (
            recovered.get("id") if isinstance(recovered, Mapping) else None
        )
        if not isinstance(task_id, str) or not task_id:
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason="reconciliation returned no task identity",
            )
        receipt = json.loads(occurrence["receipt_json"])
        coalesced = receipt.get("coalesced_missed_intervals", 0)
        scheduled_at = receipt.get("scheduled_at")
        if isinstance(scheduled_at, str):
            try:
                due_at = _utc(datetime.fromisoformat(scheduled_at))
                coalesced += max(
                    0, int((now - due_at).total_seconds() // interval_seconds)
                )
            except (TypeError, ValueError):
                pass
        self._mark_submitted(
            occurrence_key, task_id,
            {"reconciled": True, "coalesced_missed_intervals": coalesced},
        )
        return RoutineDispatch(occurrence_key, work_key, "submitted", task_id)

    def _dispatch(
        self, routine_id: str, trigger_kind: str, trigger_key: str, *,
        submit: Callable[..., Any], approval: Callable[[RoutineSpec], Any] | None,
        reconcile: Callable[[str], Any | None] | None,
        event_payload: Mapping[str, Any] | None = None,
        scheduled_at: datetime | None = None,
        schedule_cursor: datetime | None = None,
        expected_version: int | None = None,
        missed_intervals: int = 0,
    ) -> RoutineDispatch:
        spec = self.get(routine_id)
        expected_version = spec.version if expected_version is None else expected_version
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected routine version must be a positive integer")
        if type(missed_intervals) is not int or missed_intervals < 0:
            raise ValueError("missed_intervals must be a non-negative integer")
        approval_receipt: dict[str, Any] | None = None
        approval_allowed = True
        if spec.approval_required:
            approval_result: Any = False
            approval_error: str | None = None
            if approval is not None:
                try:
                    approval_result = approval(spec)
                except Exception as exc:
                    approval_error = type(exc).__name__
            if approval_result is True:
                approval_receipt = {
                    "approved": True, "evidence_type": "trusted_callback_boolean",
                }
            elif approval_result is False or approval is None or approval_error:
                approval_receipt = {
                    "approved": False,
                    "evidence_type": "trusted_callback_boolean" if approval is not None else "missing_verifier",
                }
                if approval_error:
                    approval_receipt["error_type"] = approval_error
                approval_allowed = False
            elif isinstance(approval_result, Mapping):
                try:
                    encoded_approval = json.dumps(
                        dict(approval_result), sort_keys=True, separators=(",", ":"), allow_nan=False,
                    )
                    if len(encoded_approval.encode("utf-8")) > 16_384:
                        raise ValueError("approval receipt exceeds 16 KB")
                    decision = json.loads(encoded_approval)
                    approved_value = decision.get("approved")
                    if type(approved_value) is not bool:
                        raise ValueError("approval receipt requires a boolean approved field")
                    approval_receipt = {
                        "approved": approved_value, "evidence_type": "structured",
                    }
                    for field_name in ("approval_id", "approver", "scope", "decision_digest", "expires_at"):
                        if field_name in decision:
                            approval_receipt[field_name] = decision[field_name]
                    if approved_value:
                        for field_name in ("approval_id", "approver", "scope", "decision_digest"):
                            value = approval_receipt.get(field_name)
                            if not isinstance(value, str) or not value.strip():
                                raise ValueError(f"approved receipt requires {field_name}")
                        digest_value = approval_receipt["decision_digest"]
                        if len(digest_value) != 64 or any(c not in "0123456789abcdef" for c in digest_value):
                            raise ValueError("decision_digest must be a lowercase SHA-256 digest")
                        expires_at = approval_receipt.get("expires_at")
                        if expires_at is not None:
                            expiry = _utc(datetime.fromisoformat(expires_at))
                            if expiry <= datetime.now(timezone.utc):
                                approval_receipt["approved"] = False
                    approval_allowed = approval_receipt["approved"] is True
                except (TypeError, ValueError) as exc:
                    approval_receipt = {
                        "approved": False, "evidence_type": "invalid_structured_receipt",
                        "error_type": type(exc).__name__,
                    }
                    approval_allowed = False
            else:
                approval_receipt = {
                    "approved": False, "evidence_type": "invalid_verifier_result",
                }
                approval_allowed = False
        occurrence_key = _digest({
            "routine_id": routine_id, "version": expected_version,
            "trigger_kind": trigger_kind, "trigger_key": trigger_key,
        })
        work_key = f"routine:{routine_id}:v{expected_version}:{occurrence_key[:32]}"
        if schedule_cursor is not None:
            schedule_cursor = _utc(schedule_cursor)
        trigger_digest = _digest({
            "event_payload": dict(event_payload) if event_payload is not None else None,
            "scheduled_at": _iso(scheduled_at) if scheduled_at is not None else None,
            "schedule_cursor": _iso(schedule_cursor) if schedule_cursor is not None else None,
        })
        now = _iso(datetime.now(timezone.utc))
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT version,enabled FROM routines WHERE routine_id=?",
                (routine_id,),
            ).fetchone()
            if current is None:
                db.execute("ROLLBACK")
                raise KeyError(routine_id)
            previous = db.execute(
                "SELECT status,task_id,receipt_json FROM routine_occurrences WHERE occurrence_key=?",
                (occurrence_key,),
            ).fetchone()
            resuming_approval = False
            if previous:
                previous_receipt = json.loads(previous["receipt_json"])
                if previous_receipt.get("trigger_digest") != trigger_digest:
                    db.execute("COMMIT")
                    raise ValueError("trigger identity was reused with different payload")
                if previous["status"] == "submitted":
                    db.execute("COMMIT")
                    return RoutineDispatch(occurrence_key, work_key, "duplicate", previous["task_id"])
                if previous["status"] == "blocked_approval":
                    if not approval_allowed:
                        db.execute("COMMIT")
                        return RoutineDispatch(
                            occurrence_key, work_key, "blocked_approval",
                            reason="required approval remains absent, denied, expired, or invalid",
                        )
                    if current["version"] != expected_version:
                        db.execute("COMMIT")
                        return RoutineDispatch(
                            occurrence_key, work_key, "stale",
                            reason="routine version changed before approval retry",
                        )
                    if not current["enabled"]:
                        db.execute("COMMIT")
                        return RoutineDispatch(
                            occurrence_key, work_key, "disabled",
                            reason="routine is disabled",
                        )
                    previous_receipt["approval"] = approval_receipt
                    previous_receipt["state"] = "dispatching"
                    db.execute(
                        "UPDATE routine_occurrences SET status='dispatching',receipt_json=?,updated_at=? "
                        "WHERE occurrence_key=? AND status='blocked_approval'",
                        (json.dumps(previous_receipt, sort_keys=True), now, occurrence_key),
                    )
                    resuming_approval = True
                    previous = None
                elif previous["status"] in {"dispatching", "unknown"}:
                    db.execute("COMMIT")
                    if reconcile is None:
                        return RoutineDispatch(occurrence_key, work_key, "unknown",
                                               reason="submission outcome requires authoritative reconciliation; reconcile before retry")
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
                else:
                    db.execute("COMMIT")
                    return RoutineDispatch(occurrence_key, work_key, previous["status"])
            if current["version"] != expected_version:
                db.execute("COMMIT")
                return RoutineDispatch(
                    occurrence_key, work_key, "stale",
                    reason="routine version changed before occurrence admission",
                )
            if not current["enabled"]:
                db.execute("COMMIT")
                return RoutineDispatch("", "", "disabled", reason="routine is disabled")
            if spec.approval_required and not approval_allowed:
                receipt = {
                    "state": "blocked_approval", "routine_digest": spec.digest,
                    "trigger_digest": trigger_digest,
                    "approval": approval_receipt,
                    "profile_digest": spec.profile_digest,
                    "policy_digest": spec.policy_digest,
                }
                db.execute(
                    """INSERT INTO routine_occurrences
                       (occurrence_key,routine_id,routine_version,trigger_kind,status,work_key,
                        receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (occurrence_key, routine_id, expected_version, trigger_kind, "blocked_approval",
                     work_key, json.dumps(receipt, sort_keys=True), now, now),
                )
                db.execute("COMMIT")
                return RoutineDispatch(
                    occurrence_key, work_key, "blocked_approval",
                    reason="required approval is absent, denied, expired, or invalid",
                )
            if spec.concurrency_policy == "skip":
                active_rows = db.execute(
                    "SELECT occurrence_key,work_key,status FROM routine_occurrences "
                    "WHERE routine_id=? AND occurrence_key<>? "
                    "AND status IN ('submitted','dispatching','unknown','cancellation_requested') "
                    "ORDER BY created_at DESC LIMIT 65",
                    (routine_id, occurrence_key),
                ).fetchall()
                active_rows = [dict(row) for row in active_rows]
            else:
                active_rows = []
            receipt = {
                "state": "dispatching", "routine_digest": spec.digest,
                "trigger_digest": trigger_digest,
                "scheduled_at": _iso(scheduled_at) if scheduled_at is not None else None,
                "schedule_cursor": _iso(schedule_cursor) if schedule_cursor is not None else None,
                "coalesced_missed_intervals": missed_intervals,
                "profile_digest": spec.profile_digest,
                "policy_digest": spec.policy_digest,
                "approval": approval_receipt,
            }
            if not resuming_approval:
                db.execute(
                    """INSERT INTO routine_occurrences
                       (occurrence_key,routine_id,routine_version,trigger_kind,status,work_key,
                        receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (occurrence_key, routine_id, expected_version, trigger_kind, "dispatching",
                     work_key, json.dumps(receipt, sort_keys=True), now, now),
                )
            db.execute("COMMIT")

        if active_rows:
            if len(active_rows) > 64:
                self._update_receipt(
                    occurrence_key, status="blocked_overlap",
                    updates={"state": "blocked_overlap", "overlap_resolution": "candidate_limit_exceeded"},
                )
                return RoutineDispatch(
                    occurrence_key, work_key, "blocked_overlap",
                    reason="too many prior occurrences require reconciliation; dispatch refused",
                )
            if reconcile is None:
                self._update_receipt(
                    occurrence_key, status="blocked_overlap",
                    updates={"state": "blocked_overlap", "overlap_resolution": "missing_reconciler"},
                )
                return RoutineDispatch(
                    occurrence_key, work_key, "blocked_overlap",
                    reason="overlap policy requires authoritative task-state lookup; dispatch refused",
                )
            for active_row in active_rows:
                active_work_key = active_row["work_key"]
                try:
                    active_task = reconcile(active_work_key)
                except Exception as exc:
                    self._update_receipt(
                        occurrence_key, status="blocked_overlap",
                        updates={"state": "blocked_overlap", "overlap_resolution": "reconciliation_failed",
                                 "overlap_error_type": type(exc).__name__},
                    )
                    return RoutineDispatch(
                        occurrence_key, work_key, "blocked_overlap",
                        reason=f"overlap reconciliation failed: {type(exc).__name__}",
                    )
                if active_task is None:
                    self._update_receipt(
                        occurrence_key, status="blocked_overlap",
                        updates={"state": "blocked_overlap", "overlap_resolution": "prior_task_missing",
                                 "overlap_work_key": active_work_key},
                    )
                    return RoutineDispatch(
                        occurrence_key, work_key, "blocked_overlap",
                        reason="prior occurrence has no authoritative task record; dispatch refused",
                    )
                task_state = (
                    active_task.get("state") if isinstance(active_task, Mapping)
                    else getattr(active_task, "state", None)
                )
                if task_state not in {"completed", "failed", "cancelled"}:
                    self._update_receipt(
                        occurrence_key, status="skipped_overlap",
                        updates={
                            "state": "skipped_overlap",
                            "overlap_work_key": active_work_key,
                            "overlap_task_state": task_state or "unknown",
                        },
                    )
                    return RoutineDispatch(
                        occurrence_key, work_key, "skipped_overlap",
                        reason="an earlier occurrence is not terminal",
                    )
                self._update_receipt(
                    active_row["occurrence_key"], status=task_state,
                    updates={"state": task_state, "terminal_reconciled": True},
                )

        payload = dict(spec.payload)
        if event_payload is not None:
            payload["_auren_trigger_event"] = dict(event_payload)
        if scheduled_at is not None:
            payload["_auren_scheduled_at"] = _iso(scheduled_at)
        if missed_intervals:
            payload["_auren_coalesced_missed_intervals"] = missed_intervals
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

    def _update_receipt(
        self, occurrence_key: str, *, status: str, updates: Mapping[str, Any],
        task_id: str | None = None,
    ) -> None:
        """Update receipt state without discarding immutable trigger identity metadata."""
        now = _iso(datetime.now(timezone.utc))
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT receipt_json FROM routine_occurrences WHERE occurrence_key=?",
                (occurrence_key,),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise KeyError(occurrence_key)
            receipt = json.loads(row["receipt_json"])
            receipt.update(dict(updates))
            db.execute(
                "UPDATE routine_occurrences SET status=?,task_id=COALESCE(?,task_id),"
                "receipt_json=?,updated_at=? WHERE occurrence_key=?",
                (status, task_id, json.dumps(receipt, sort_keys=True), now, occurrence_key),
            )
            db.execute("COMMIT")

    def _mark_submitted(self, occurrence_key: str, task_id: str, receipt: Mapping[str, Any]) -> None:
        self._update_receipt(
            occurrence_key, status="submitted", task_id=task_id,
            updates={"state": "submitted", **dict(receipt)},
        )

    def _mark_unknown(self, occurrence_key: str, error_type: str) -> None:
        self._update_receipt(
            occurrence_key, status="unknown",
            updates={"state": "unknown", "error_type": error_type},
        )

    def cancel_inflight(
        self, routine_id: str, *,
        reconcile: Callable[[str], Any | None],
        request_cancel: Callable[[str, str], bool],
        reason: str,
        limit: int = 64,
    ) -> tuple[RoutineDispatch, ...]:
        """Request cancellation through the canonical controller for all live occurrences.

        A cancellation request is not reported as completion. The task controller
        remains the authority for the eventual terminal task state.
        """
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("cancellation reason must be non-empty text of at most 1000 characters")
        if not callable(reconcile) or not callable(request_cancel):
            raise TypeError("authoritative reconciliation and cancellation callbacks are required")
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("limit must be between 1 and 256")
        self.get(routine_id)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT occurrence_key,work_key,task_id,status FROM routine_occurrences "
                "WHERE routine_id=? AND status IN ('dispatching','unknown','submitted','cancellation_requested') "
                "ORDER BY created_at LIMIT ?",
                (routine_id, limit),
            ).fetchall()
        reason_digest = hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()
        results: list[RoutineDispatch] = []
        for row in rows:
            occurrence_key = str(row["occurrence_key"])
            work_key = str(row["work_key"])
            try:
                task = reconcile(work_key)
            except Exception as exc:
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "unknown",
                    reason=f"task reconciliation failed: {type(exc).__name__}",
                ))
                continue
            if task is None:
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "unknown",
                    reason="canonical task record is unavailable; cancellation outcome is unknown",
                ))
                continue
            task_id = getattr(task, "id", None) or (
                task.get("id") if isinstance(task, Mapping) else None
            )
            task_state = getattr(task, "state", None) or (
                task.get("state") if isinstance(task, Mapping) else None
            )
            if not isinstance(task_id, str) or not task_id:
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "unknown",
                    reason="canonical task lookup returned no task identity",
                ))
                continue
            if task_state in {"completed", "failed", "cancelled"}:
                self._update_receipt(
                    occurrence_key, status=str(task_state),
                    task_id=task_id,
                    updates={"state": str(task_state), "terminal_observed_at": _iso(datetime.now(timezone.utc))},
                )
                results.append(RoutineDispatch(occurrence_key, work_key, str(task_state), task_id))
                continue
            try:
                accepted = request_cancel(task_id, reason.strip()) is True
            except Exception as exc:
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "unknown", task_id,
                    reason=f"controller cancellation request failed: {type(exc).__name__}",
                ))
                continue
            if accepted:
                self._update_receipt(
                    occurrence_key, status="cancellation_requested",
                    task_id=task_id,
                    updates={
                        "state": "cancellation_requested",
                        "cancellation_reason_digest": reason_digest,
                        "cancellation_requested_at": _iso(datetime.now(timezone.utc)),
                        "task_state_at_cancellation": task_state,
                    },
                )
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "cancellation_requested", task_id,
                    reason="request accepted; task remains non-terminal until controller confirms cancellation",
                ))
            else:
                results.append(RoutineDispatch(
                    occurrence_key, work_key, "not_cancelled", task_id,
                    reason="controller did not accept cancellation; inspect authoritative task state",
                ))
        return tuple(results)

    def occurrence(self, occurrence_key: str) -> Mapping[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM routine_occurrences WHERE occurrence_key=?", (occurrence_key,)
            ).fetchone()
        return dict(row) if row else None

    def request_cancellation(
        self, occurrence_key: str, *, cancel: Callable[..., bool], reason: str,
    ) -> RoutineDispatch:
        """Request task cancellation through the canonical controller.

        The occurrence remains nonterminal until the controller confirms its
        final state. This method records the request, never a false cancellation.
        """
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("cancellation reason must be non-empty text of at most 1000 characters")
        occurrence = self.occurrence(occurrence_key)
        if occurrence is None:
            raise KeyError(occurrence_key)
        task_id = occurrence.get("task_id")
        work_key = str(occurrence["work_key"])
        status = str(occurrence["status"])
        if status in {"completed", "failed", "cancelled", "skipped_overlap", "blocked_overlap"}:
            return RoutineDispatch(occurrence_key, work_key, status, task_id)
        if not isinstance(task_id, str) or not task_id:
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                reason="occurrence has no reconciled task ID; cancellation was not attempted",
            )
        try:
            accepted = cancel(task_id, reason=reason.strip())
        except Exception as exc:
            self._update_receipt(
                occurrence_key, status=status,
                updates={"cancellation_error_type": type(exc).__name__},
            )
            return RoutineDispatch(
                occurrence_key, work_key, "unknown",
                task_id, f"cancellation request failed ({type(exc).__name__})",
            )
        if accepted is not True:
            return RoutineDispatch(
                occurrence_key, work_key, "not_cancellable", task_id,
                "controller did not accept cancellation; task state remains authoritative",
            )
        reason_digest = hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()
        requested_at = _iso(datetime.now(timezone.utc))
        self._update_receipt(
            occurrence_key, status="cancellation_requested",
            updates={
                "state": "cancellation_requested",
                "cancellation_requested_at": requested_at,
                "cancellation_reason_digest": reason_digest,
            },
        )
        return RoutineDispatch(
            occurrence_key, work_key, "cancellation_requested", task_id,
        )


__all__ = ["DurableRoutineRegistry", "RoutineDispatch", "RoutineSpec", "verify_hmac_webhook_signature"]
