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


class RecoveryJournalConsistencyError(RuntimeError):
    """Raised when recovery ledger and journal cannot be reconciled safely."""


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

    @staticmethod
    def _checkpoint_digest(checkpoint: Any) -> str:
        material = {
            "status": checkpoint.status if checkpoint is not None else "missing",
            "head_sha": checkpoint.head_sha if checkpoint is not None else "",
            "payload": checkpoint.payload if checkpoint is not None else {},
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def reconcile_ledger(self, ledger: Any, work_id: str, phase: str = "execution") -> dict[str, Any]:
        """Compare receipts to durable attempt order before permitting resumed execution.

        No receipts means a legacy run predating journaling and is reported, not
        silently rewritten. Once journaling exists, every later started attempt
        must have its preceding decision receipt. A decision persisted just before
        its attempt row is an interrupted boundary and is blocked for explicit
        reconciliation rather than replayed.
        """
        if not callable(getattr(ledger, "attempts", None)) or not callable(getattr(ledger, "get_checkpoint", None)):
            raise TypeError("ledger must expose attempts() and get_checkpoint()")
        attempts = tuple(item for item in ledger.attempts(work_id) if item.phase == phase)
        receipts = self.list_for_work(work_id, phase)
        if not receipts:
            return {"status": "legacy_unjournaled", "attempts": len(attempts), "receipts": 0, "missing": [], "pending_decision": None}
        by_number = {item.attempt_number: item for item in receipts}
        first_receipt = min(by_number)
        missing = [
            number for number in range(max(2, first_receipt), len(attempts) + 1)
            if number not in by_number
        ]
        if missing:
            return {
                "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                "missing": missing, "pending_decision": None,
                "reason": "a started recovery attempt has no preceding durable decision receipt",
            }
        for receipt in receipts:
            number = receipt.attempt_number
            if number > len(attempts) + 1:
                return {
                    "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                    "missing": [], "pending_decision": number,
                    "reason": "decision receipt is ahead of the ledger by more than one attempt",
                }
            attempt = attempts[number - 1] if number <= len(attempts) else None
            if receipt.action in {"block", "stop"}:
                if attempt is not None:
                    return {
                        "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                        "missing": [], "pending_decision": number,
                        "reason": "a blocked decision has a corresponding started attempt",
                    }
                if number == len(attempts) + 1:
                    return {
                        "status": "blocked_decision", "attempts": len(attempts), "receipts": len(receipts),
                        "missing": [], "pending_decision": number,
                        "reason": receipt.reason_code,
                    }
            if attempt is not None:
                if receipt.action == "alternate" and attempt.hypothesis != receipt.strategy_id:
                    return {
                        "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                        "missing": [], "pending_decision": number,
                        "reason": "alternate strategy receipt does not match the started attempt",
                    }
                if receipt.action == "retry" and number > 1 and attempt.hypothesis != attempts[number - 2].hypothesis:
                    return {
                        "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                        "missing": [], "pending_decision": number,
                        "reason": "retry receipt does not match the preceding hypothesis",
                    }
            elif receipt.action in {"retry", "alternate"}:
                checkpoint = ledger.get_checkpoint(work_id, phase)
                actual_digest = self._checkpoint_digest(checkpoint)
                if actual_digest != receipt.checkpoint_digest:
                    return {
                        "status": "inconsistent", "attempts": len(attempts), "receipts": len(receipts),
                        "missing": [], "pending_decision": number,
                        "reason": "pending decision checkpoint digest does not match the durable checkpoint",
                    }
                return {
                    "status": "pending_decision", "attempts": len(attempts), "receipts": len(receipts),
                    "missing": [], "pending_decision": number,
                    "reason": "decision persisted before attempt claim; do not replay without an explicit recovery path",
                }
        return {
            "status": "consistent", "attempts": len(attempts), "receipts": len(receipts),
            "missing": [], "pending_decision": None,
        }

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
