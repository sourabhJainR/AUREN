"""Durable, append-only receipts for bounded recovery decisions.

This journal records policy outcomes; it does not execute recovery or authorize effects.
Do not store prompts, credentials, raw provider output, or other secret-bearing data here.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_TOKEN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = frozenset({"stop", "block", "alternate", "retry"})
_SIDE_EFFECT_STATES = frozenset({"none", "confirmed", "reconciled", "ambiguous"})


def _token(value: str, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{field} must be a bounded identifier token")
    return value


@dataclass(frozen=True, slots=True)
class RecoveryDecisionReceipt:
    work_id: str
    phase: str
    attempt_number: int
    action: str
    strategy_id: str | None
    failure_class: str
    attempts_remaining: int
    checkpoint_digest: str
    side_effect_state: str
    reason_code: str
    created_at: str
    decision_digest: str


class RecoveryDecisionJournal:
    """SQLite-backed immutable recovery decisions with idempotent writes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS recovery_decisions (
                    work_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    decision_json TEXT NOT NULL,
                    decision_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (work_id, phase, attempt_number)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _payload(
        *, work_id: str, phase: str, attempt_number: int, action: str,
        strategy_id: str | None, failure_class: str, attempts_remaining: int,
        checkpoint_digest: str, side_effect_state: str, reason_code: str,
    ) -> dict[str, object]:
        _token(work_id, "work_id")
        _token(phase, "phase")
        _token(failure_class, "failure_class")
        _token(reason_code, "reason_code")
        if type(attempt_number) is not int or attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        if type(attempts_remaining) is not int or attempts_remaining < 0:
            raise ValueError("attempts_remaining must be a non-negative integer")
        if action not in _ACTIONS:
            raise ValueError("unsupported recovery action")
        if strategy_id is not None:
            _token(strategy_id, "strategy_id")
        if not isinstance(checkpoint_digest, str) or not _DIGEST.fullmatch(checkpoint_digest):
            raise ValueError("checkpoint_digest must be a lowercase SHA-256 digest")
        if side_effect_state not in _SIDE_EFFECT_STATES:
            raise ValueError("unsupported side_effect_state")
        if action in {"stop", "block"} and strategy_id is not None:
            raise ValueError("stop/block decisions cannot select a strategy")
        if action == "alternate" and strategy_id is None:
            raise ValueError("alternate decisions require a strategy_id")
        if action in {"stop", "block"} and not reason_code:
            raise ValueError("stop/block decisions require a reason_code")
        return {
            "work_id": work_id, "phase": phase, "attempt_number": attempt_number,
            "action": action, "strategy_id": strategy_id, "failure_class": failure_class,
            "attempts_remaining": attempts_remaining, "checkpoint_digest": checkpoint_digest,
            "side_effect_state": side_effect_state, "reason_code": reason_code,
        }

    def record(
        self, *, work_id: str, phase: str, attempt_number: int, action: str,
        strategy_id: str | None, failure_class: str, attempts_remaining: int,
        checkpoint_digest: str, side_effect_state: str, reason_code: str,
    ) -> RecoveryDecisionReceipt:
        payload = self._payload(
            work_id=work_id, phase=phase, attempt_number=attempt_number, action=action,
            strategy_id=strategy_id, failure_class=failure_class,
            attempts_remaining=attempts_remaining, checkpoint_digest=checkpoint_digest,
            side_effect_state=side_effect_state, reason_code=reason_code,
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT decision_json, decision_digest, created_at FROM recovery_decisions "
                "WHERE work_id=? AND phase=? AND attempt_number=?",
                (work_id, phase, attempt_number),
            ).fetchone()
            if row is not None:
                if row["decision_digest"] != digest or row["decision_json"] != canonical:
                    raise ValueError("recovery decision key already contains a different receipt")
                created_at = row["created_at"]
            else:
                created_at = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "INSERT INTO recovery_decisions "
                    "(work_id, phase, attempt_number, decision_json, decision_digest, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (work_id, phase, attempt_number, canonical, digest, created_at),
                )
        return RecoveryDecisionReceipt(**payload, created_at=created_at, decision_digest=digest)

    def get(self, work_id: str, phase: str, attempt_number: int) -> RecoveryDecisionReceipt | None:
        _token(work_id, "work_id")
        _token(phase, "phase")
        if type(attempt_number) is not int or attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        with self._connect() as db:
            row = db.execute(
                "SELECT decision_json, decision_digest, created_at FROM recovery_decisions "
                "WHERE work_id=? AND phase=? AND attempt_number=?",
                (work_id, phase, attempt_number),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["decision_json"])
        return RecoveryDecisionReceipt(
            **payload, created_at=row["created_at"], decision_digest=row["decision_digest"]
        )

    def list_for_work(self, work_id: str, phase: str) -> tuple[RecoveryDecisionReceipt, ...]:
        _token(work_id, "work_id")
        _token(phase, "phase")
        with self._connect() as db:
            rows = db.execute(
                "SELECT decision_json, decision_digest, created_at FROM recovery_decisions "
                "WHERE work_id=? AND phase=? ORDER BY attempt_number",
                (work_id, phase),
            ).fetchall()
        return tuple(
            RecoveryDecisionReceipt(
                **json.loads(row["decision_json"]), created_at=row["created_at"],
                decision_digest=row["decision_digest"],
            )
            for row in rows
        )
