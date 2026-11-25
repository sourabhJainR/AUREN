"""Guard-aware, durable intra-organization messaging primitives for AUREN.

This is a communication ledger/adapter, not a task scheduler or execution authority.
Consequential work must be submitted through the canonical controller.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

SCHEMA = "auren-org-message-v1"
KINDS = frozenset({"QUERY", "RESPONSE", "TASK_REQUEST", "TASK_ACCEPTED",
    "TASK_REJECTED", "HANDOFF", "STATUS_UPDATE", "BLOCKED",
    "REVIEW_REQUEST", "REVIEW_RESULT", "EVIDENCE_SUBMITTED", "CANCEL"})
MAX_BODY_BYTES = 64 * 1024


class MessageDenied(PermissionError):
    """Message rejected by organization or Guard admission."""


class MessageConflict(ValueError):
    """Idempotency key reused for a different message."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    organization_id: str
    role: str
    capabilities: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MessageReceipt:
    message_id: str
    thread_id: str
    state: str
    digest: str


class OrganizationMessaging:
    """SQLite-backed message ledger with guarded dispatch and idempotent enqueue.

    Membership is supplied by the trusted host; callers cannot create identities.
    authorize() must validate policy/task/workspace context before delivery.
    deliver() is a proposal/notification adapter, never privileged execution.
    """

    def __init__(self, database: str | Path, organization_id: str,
                 members: Mapping[str, AgentIdentity],
                 authorize: Callable[[AgentIdentity, AgentIdentity, Mapping[str, Any]], bool],
                 deliver: Callable[[AgentIdentity, Mapping[str, Any]], Any],
                 *, max_messages: int = 10000, max_attempts: int = 4):
        if not organization_id or not organization_id.strip():
            raise ValueError("organization_id is required")
        if not callable(authorize) or not callable(deliver):
            raise TypeError("authorize and deliver callbacks are required")
        if not 1 <= max_messages <= 1000000 or not 1 <= max_attempts <= 100:
            raise ValueError("invalid ledger bounds")
        self.organization_id = organization_id
        self.members = dict(members)
        for key, identity in self.members.items():
            if key != identity.agent_id or identity.organization_id != organization_id:
                raise ValueError("membership identity mismatch")
        self.authorize, self.deliver = authorize, deliver
        self.max_messages, self.max_attempts = max_messages, max_attempts
        self.db = sqlite3.connect(str(database), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages(
            message_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            organization_id TEXT NOT NULL, sender_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL, kind TEXT NOT NULL, thread_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL, body TEXT NOT NULL, body_digest TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, expires_at REAL, last_error TEXT)""")
        self.db.execute("""CREATE INDEX IF NOT EXISTS messages_thread
            ON messages(thread_id, created_at)""")

    def close(self):
        self.db.close()

    def enqueue(self, sender_id: str, recipient_id: str, kind: str,
                body: Mapping[str, Any], *, idempotency_key: str,
                thread_id: str | None = None, correlation_id: str | None = None,
                expires_at: float | None = None) -> MessageReceipt:
        sender, recipient = self.members.get(sender_id), self.members.get(recipient_id)
        if sender is None or recipient is None:
            raise MessageDenied("sender or recipient is not an organization member")
        if sender_id == recipient_id:
            raise ValueError("self-messaging is not supported")
        if kind not in KINDS:
            raise ValueError("unsupported message kind")
        if not isinstance(body, Mapping) or not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("body and idempotency_key are required")
        encoded = _canonical(dict(body))
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("message payload exceeds size limit")
        now = time.time()
        if expires_at is not None and (type(expires_at) not in (int, float) or expires_at <= now):
            raise ValueError("expires_at must be a future timestamp")
        envelope = {"schema_version": SCHEMA, "organization_id": self.organization_id,
            "sender_id": sender_id, "recipient_id": recipient_id, "kind": kind,
            "thread_id": thread_id or str(uuid4()), "correlation_id": correlation_id or str(uuid4()),
            "body": dict(body), "body_digest": _digest(dict(body))}
        if not self.authorize(sender, recipient, envelope):
            raise MessageDenied("organization Guard denied message admission")
        with self.db:
            count = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            existing = self.db.execute("SELECT * FROM messages WHERE idempotency_key=?",
                (idempotency_key,)).fetchone()
            if existing:
                if existing["body_digest"] != envelope["body_digest"] or existing["sender_id"] != sender_id or existing["recipient_id"] != recipient_id or existing["kind"] != kind:
                    raise MessageConflict("idempotency key conflicts with existing message")
                return self._receipt(existing)
            if count >= self.max_messages:
                raise MessageDenied("message ledger capacity reached")
            mid = str(uuid4())
            self.db.execute("""INSERT INTO messages(message_id,idempotency_key,organization_id,
                sender_id,recipient_id,kind,thread_id,correlation_id,body,body_digest,state,
                created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid,idempotency_key,self.organization_id,sender_id,recipient_id,kind,
                 envelope["thread_id"],envelope["correlation_id"],encoded.decode(),
                 envelope["body_digest"],"queued",now,expires_at))
            row = self.db.execute("SELECT * FROM messages WHERE message_id=?", (mid,)).fetchone()
        return self._receipt(row)

    @staticmethod
    def _receipt(row) -> MessageReceipt:
        record = {k: row[k] for k in ("message_id","organization_id","sender_id",
            "recipient_id","kind","thread_id","correlation_id","body_digest","state")}
        return MessageReceipt(row["message_id"], row["thread_id"], row["state"], _digest(record))

    def dispatch(self, message_id: str) -> MessageReceipt:
        row = self.db.execute("SELECT * FROM messages WHERE message_id=?",
            (message_id,)).fetchone()
        if row is None:
            raise KeyError(message_id)
        if row["state"] in ("delivered","dead_letter","cancelled"):
            return self._receipt(row)
        if row["expires_at"] is not None and row["expires_at"] <= time.time():
            self._set_state(message_id, "expired")
            return self._receipt(self.db.execute("SELECT * FROM messages WHERE message_id=?",
                (message_id,)).fetchone())
        if row["attempts"] >= self.max_attempts:
            self._set_state(message_id, "dead_letter")
            return self._receipt(self.db.execute("SELECT * FROM messages WHERE message_id=?",
                (message_id,)).fetchone())
        sender, recipient = self.members.get(row["sender_id"]), self.members.get(row["recipient_id"])
        if not sender or not recipient:
            self._set_state(message_id, "denied", "membership unavailable")
            raise MessageDenied("membership unavailable at dispatch")
        envelope = {"schema_version":SCHEMA,"organization_id":row["organization_id"],
            "sender_id":row["sender_id"],"recipient_id":row["recipient_id"],
            "kind":row["kind"],"thread_id":row["thread_id"],
            "correlation_id":row["correlation_id"],"body":json.loads(row["body"]),
            "body_digest":row["body_digest"]}
        if not self.authorize(sender, recipient, envelope):
            self._set_state(message_id, "denied", "Guard denied at dispatch")
            raise MessageDenied("Guard denied message dispatch")
        self._set_state(message_id, "in_flight", attempts=row["attempts"]+1)
        try:
            self.deliver(recipient, envelope)
        except Exception as exc:
            # Outcome may be ambiguous if the receiver accepted before failure.
            # Consumer must reconcile/idempotently process message_id.
            self._set_state(message_id, "retryable", type(exc).__name__)
        else:
            self._set_state(message_id, "delivered")
        return self._receipt(self.db.execute("SELECT * FROM messages WHERE message_id=?",
            (message_id,)).fetchone())

    def _set_state(self, message_id, state, error=None, attempts=None):
        with self.db:
            if attempts is None:
                self.db.execute("UPDATE messages SET state=?,last_error=? WHERE message_id=?",
                    (state,error,message_id))
            else:
                self.db.execute("UPDATE messages SET state=?,attempts=?,last_error=? WHERE message_id=?",
                    (state,attempts,error,message_id))

    def cancel(self, message_id: str, requester_id: str) -> MessageReceipt:
        row = self.db.execute("SELECT * FROM messages WHERE message_id=?",
            (message_id,)).fetchone()
        if not row:
            raise KeyError(message_id)
        if requester_id not in (row["sender_id"], row["recipient_id"]):
            raise MessageDenied("only sender or recipient may cancel")
        if row["state"] in ("in_flight","delivered"):
            raise MessageDenied("in-flight/delivered message requires reconciliation")
        self._set_state(message_id,"cancelled")
        return self._receipt(self.db.execute("SELECT * FROM messages WHERE message_id=?",
            (message_id,)).fetchone())

    def thread(self, thread_id: str) -> list[dict[str, Any]]:
        rows=self.db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY created_at",
            (thread_id,)).fetchall()
        return [{k:r[k] for k in ("message_id","sender_id","recipient_id","kind",
            "thread_id","correlation_id","body","state","attempts","created_at",
            "expires_at","last_error","body_digest")} for r in rows]

    def pending(self, limit: int=100) -> list[str]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return [r[0] for r in self.db.execute(
            "SELECT message_id FROM messages WHERE state IN ('queued','retryable') "
            "ORDER BY created_at LIMIT ?",(limit,)).fetchall()]
