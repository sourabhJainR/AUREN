"""SQLite-backed idempotency gate for consequential side effects.

This guarantees no automatic repeat after an ambiguous interruption. It does not
claim exactly-once external execution; an uncertain outcome requires reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


class IdempotencyConflict(ValueError):
    """A key was reused for a different payload or immutable receipt."""


class InvalidSideEffectTransition(RuntimeError):
    """The requested side-effect state transition is not permitted."""


@dataclass(frozen=True)
class SideEffectDecision:
    action: str
    idempotency_key: str
    payload_digest: str
    receipt: Mapping[str, Any] | None = None
    state: str = ""


class DurableSideEffectLedger:
    """Persist downstream idempotency decisions before calling external systems.

    Only an initial begin, or an explicitly operator-authorized retry, returns
    action="execute". An interrupted executing record is converted to
    reconcile_required and is never silently re-executed.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        retry_authorizer: Callable[[str, str, str, str], bool] | None = None,
        reconciliation_verifier: Callable[[str, str, Mapping[str, Any]], bool] | None = None,
    ):
        self.database_path = Path(database_path)
        self._retry_authorizer = retry_authorizer
        self._reconciliation_verifier = reconciliation_verifier
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS durable_side_effects (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'executing', 'completed', 'reconcile_required', 'retry_authorized'
                    )),
                    receipt_json TEXT,
                    reason TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS durable_side_effect_retry_authorizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    operator_reason TEXT NOT NULL,
                    authorization_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self):
        """Open one connection per operation and always release its file handle.

        sqlite3.Connection's context manager manages transactions but does not
        close the connection. Explicitly close here so Windows can safely remove
        temporary stores after failures and process-recovery tests.
        """
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            yield db
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> str:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def _digest(cls, payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(cls._encode(payload).encode("utf-8")).hexdigest()

    def begin(self, idempotency_key: str, payload: Mapping[str, Any]) -> SideEffectDecision:
        key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if not key:
            raise ValueError("idempotency_key must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a mapping")
        digest = self._digest(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM durable_side_effects WHERE idempotency_key=?", (key,)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO durable_side_effects "
                    "(idempotency_key,payload_digest,state,updated_at) VALUES (?,?,?,?)",
                    (key, digest, "executing", self._now()),
                )
                db.commit()
                return SideEffectDecision("execute", key, digest, state="executing")
            if row["payload_digest"] != digest:
                db.rollback()
                raise IdempotencyConflict("idempotency key is bound to a different payload")
            state = row["state"]
            if state == "completed":
                receipt = json.loads(row["receipt_json"])
                db.commit()
                return SideEffectDecision("replay", key, digest, receipt, state)
            if state == "retry_authorized":
                db.execute(
                    "UPDATE durable_side_effects SET state='executing', reason=NULL, updated_at=? "
                    "WHERE idempotency_key=?", (self._now(), key)
                )
                db.commit()
                return SideEffectDecision("execute", key, digest, state="executing")
            if state == "executing":
                db.execute(
                    "UPDATE durable_side_effects SET state='reconcile_required', "
                    "reason=COALESCE(reason, 'prior execution may have been interrupted'), updated_at=? "
                    "WHERE idempotency_key=?", (self._now(), key)
                )
                db.commit()
                return SideEffectDecision("reconcile", key, digest, state="reconcile_required")
            db.commit()
            return SideEffectDecision("reconcile", key, digest, state=state)

    def complete(self, idempotency_key: str, receipt: Mapping[str, Any]) -> SideEffectDecision:
        if not isinstance(receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        encoded = self._encode(receipt)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM durable_side_effects WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                db.rollback()
                raise InvalidSideEffectTransition("unknown side effect")
            if row["state"] == "completed":
                if row["receipt_json"] != encoded:
                    db.rollback()
                    raise IdempotencyConflict("completed receipt is immutable")
                db.commit()
                return SideEffectDecision(
                    "replay", idempotency_key, row["payload_digest"], json.loads(encoded), "completed"
                )
            if row["state"] != "executing":
                db.rollback()
                raise InvalidSideEffectTransition("side effect is not executing")
            db.execute(
                "UPDATE durable_side_effects SET state='completed', receipt_json=?, reason=NULL, updated_at=? "
                "WHERE idempotency_key=?", (encoded, self._now(), idempotency_key)
            )
            db.commit()
            return SideEffectDecision(
                "replay", idempotency_key, row["payload_digest"], json.loads(encoded), "completed"
            )

    def require_reconciliation(self, idempotency_key: str, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("a non-empty reconciliation reason is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM durable_side_effects WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None or row["state"] not in {"executing", "reconcile_required"}:
                db.rollback()
                raise InvalidSideEffectTransition("side effect is not awaiting reconciliation")
            db.execute(
                "UPDATE durable_side_effects SET state='reconcile_required', reason=?, updated_at=? "
                "WHERE idempotency_key=?", (reason.strip(), self._now(), idempotency_key)
            )
            db.commit()

    def record_reconciled_effect(
        self,
        idempotency_key: str,
        *,
        evidence_ref: str,
        receipt: Mapping[str, Any],
    ) -> SideEffectDecision:
        """Record a provider-confirmed effect after an ambiguous interruption.

        A trusted host verifier must validate provider evidence before the
        immutable receipt can close reconciliation. This is not a redispatch.
        """
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("evidence_ref is required")
        if not isinstance(receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        if self._reconciliation_verifier is None:
            raise PermissionError("trusted reconciliation verifier is required")
        try:
            verified = bool(self._reconciliation_verifier(
                idempotency_key, evidence_ref.strip(), dict(receipt)
            ))
        except Exception as exc:
            raise PermissionError("trusted reconciliation verification failed closed") from exc
        if not verified:
            raise PermissionError("provider reconciliation evidence was rejected")
        encoded = self._encode(receipt)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM durable_side_effects WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None or row["state"] != "reconcile_required":
                db.rollback()
                raise InvalidSideEffectTransition("side effect is not awaiting reconciliation")
            db.execute(
                "UPDATE durable_side_effects SET state='completed', receipt_json=?, reason=?, updated_at=? "
                "WHERE idempotency_key=? AND state='reconcile_required'",
                (encoded, "reconciled from provider evidence: " + evidence_ref.strip(), self._now(), idempotency_key),
            )
            db.commit()
            return SideEffectDecision(
                "reconciled", idempotency_key, row["payload_digest"], dict(receipt), "completed"
            )

    def authorize_retry(
        self,
        idempotency_key: str,
        *,
        operator_reason: str,
        operator_id: str,
        evidence_ref: str,
        confirmed_no_effect: bool,
    ) -> None:
        """Authorize one retry only through a trusted host-supplied verifier.

        The boolean is an additional assertion, not an authority credential. The
        injected verifier must validate operator authority and the referenced
        provider/operator evidence; absent or failing verifiers deny the retry.
        """
        if not confirmed_no_effect:
            raise ValueError("retry requires confirmation that no external effect occurred")
        for value, name in (
            (operator_reason, "operator_reason"),
            (operator_id, "operator_id"),
            (evidence_ref, "evidence_ref"),
            (idempotency_key, "idempotency_key"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self._retry_authorizer is None:
            raise PermissionError("trusted retry authorizer is required")
        try:
            authorized = bool(self._retry_authorizer(
                idempotency_key, operator_id.strip(), evidence_ref.strip(), operator_reason.strip()
            ))
        except Exception as exc:
            raise PermissionError("trusted retry authorization failed closed") from exc
        if not authorized:
            raise PermissionError("trusted retry authorizer rejected the evidence")
        audit_reason = json.dumps({
            "operator_reason": operator_reason.strip(),
            "operator_id": operator_id.strip(),
            "evidence_ref": evidence_ref.strip(),
        }, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM durable_side_effects WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None or row["state"] != "reconcile_required":
                db.rollback()
                raise InvalidSideEffectTransition("only reconciled side effects may be retried")
            created_at = self._now()
            authorization = {
                "idempotency_key": idempotency_key,
                "operator_id": operator_id.strip(),
                "evidence_ref": evidence_ref.strip(),
                "operator_reason": operator_reason.strip(),
                "created_at": created_at,
            }
            db.execute(
                "INSERT INTO durable_side_effect_retry_authorizations "
                "(idempotency_key,operator_id,evidence_ref,operator_reason,authorization_digest,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    idempotency_key, operator_id.strip(), evidence_ref.strip(),
                    operator_reason.strip(), self._digest(authorization), created_at,
                ),
            )
            db.execute(
                "UPDATE durable_side_effects SET state='retry_authorized', reason=?, updated_at=? "
                "WHERE idempotency_key=?", (audit_reason, created_at, idempotency_key)
            )
            db.commit()
    def retry_authorizations(self, idempotency_key: str) -> tuple[Mapping[str, Any], ...]:
        """Return immutable operator/evidence audit records for a retry key."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT idempotency_key,operator_id,evidence_ref,operator_reason,authorization_digest,created_at "
                "FROM durable_side_effect_retry_authorizations WHERE idempotency_key=? ORDER BY id",
                (idempotency_key,),
            ).fetchall()
        results = tuple(dict(row) for row in rows)
        for row in results:
            payload = {
                "idempotency_key": row["idempotency_key"],
                "operator_id": row["operator_id"],
                "evidence_ref": row["evidence_ref"],
                "operator_reason": row["operator_reason"],
                "created_at": row["created_at"],
            }
            if self._digest(payload) != row["authorization_digest"]:
                raise IdempotencyConflict("retry authorization audit digest mismatch")
        return results

    def get(self, idempotency_key: str) -> Mapping[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM durable_side_effects WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        encoded = result.pop("receipt_json")
        result["receipt"] = json.loads(encoded) if encoded is not None else None
        return result
