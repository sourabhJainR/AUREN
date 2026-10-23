"""Durable, content-bound human approvals for resumable AUREN operations.

The store shares the ExecutionController SQLite database when constructed with a
controller. Approval decisions are durable and auditable; side-effect outcomes remain
owned by DurableSideEffectLedger and must be reconciled there before approval state
is finalized after an ambiguous result.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

from .action_governance import ActionRequest, ApprovalReceipt

TERMINAL_STATES = frozenset({"rejected", "expired", "cancelled", "superseded", "completed", "failed"})
STATES = frozenset({
    "pending", "approved", "rejected", "expired", "cancelled", "superseded",
    "executing", "completed", "failed", "outcome_unknown",
})
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"pending", "approved", "rejected", "expired", "cancelled", "superseded"}),
    "approved": frozenset({"executing", "expired", "cancelled", "superseded"}),
    "executing": frozenset({"completed", "failed", "outcome_unknown"}),
    "outcome_unknown": frozenset({"completed", "failed"}),
    "rejected": frozenset(), "expired": frozenset(), "cancelled": frozenset(),
    "superseded": frozenset(), "completed": frozenset(), "failed": frozenset(),
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ApprovalConflict(ValueError):
    """A stable operation key was reused for a different or incompatible approval."""


class InvalidApprovalTransition(RuntimeError):
    """An approval or operation state transition is not permitted."""


class ApprovalPending(Exception):
    """Suspend a durable task at an approval boundary with a small safe checkpoint."""

    _FORBIDDEN_KEY_PARTS = (
        "secret", "password", "credential", "token", "prompt", "answer",
        "draft", "content", "response", "authorization", "api_key", "private_key",
    )
    # Defense in depth for values stored under otherwise harmless checkpoint keys.
    # Checkpoints are metadata only; these patterns catch common credential formats,
    # not every possible secret. Callers must still store references/digests, never
    # raw prompts, payloads, credentials, or provider responses.
    _SENSITIVE_VALUE_PATTERNS = (
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,})\b"),
        re.compile(r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|private[_-]?key|authorization)\s*[:=]\s*['\"]?[^,\s'\"]{4,}", re.IGNORECASE),
        re.compile(r"\bhttps?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
        re.compile(r"[?&](?:access_token|token|api_key|key|password|client_secret)=([^&#\s]{4,})", re.IGNORECASE),
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    )

    def __init__(self, approval_id: str, checkpoint: Mapping[str, Any]):
        self.approval_id = _required(approval_id, "approval_id")
        if not isinstance(checkpoint, Mapping) or not checkpoint:
            raise ValueError("approval checkpoint must be a non-empty mapping")
        payload = dict(checkpoint)
        def validate(value: Any, path: str = "") -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    name = _required(str(key), "checkpoint key").lower()
                    if any(part in name for part in self._FORBIDDEN_KEY_PARTS):
                        raise ValueError("checkpoint must store content references/digests, not prompts, drafts, or secrets")
                    validate(child, f"{path}.{name}")
            elif isinstance(value, (list, tuple)):
                for child in value:
                    validate(child, path)
            elif isinstance(value, str):
                if any(pattern.search(value) for pattern in self._SENSITIVE_VALUE_PATTERNS):
                    raise ValueError("checkpoint must not contain secret-like values; store a reference or digest instead")
                if len(value) > 2048:
                    raise ValueError("checkpoint strings must be references or bounded identifiers")
            elif value is not None and type(value) not in {bool, int, float}:
                raise ValueError("checkpoint must be JSON-safe")
        validate(payload)
        encoded = _canonical(payload)
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("approval checkpoint exceeds 16 KiB")
        self.checkpoint = payload
        super().__init__(f"approval pending: {self.approval_id}")


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    task_id: str
    operation_id: str
    requested_action: str
    request_fingerprint: str
    artifact_digest: str
    evidence_digest: str
    policy_digest: str
    requester_id: str
    required_role: str
    scope: str
    expires_at: str
    required_approvals: int
    separation_of_duties: bool
    status: str
    version: int
    created_at: str
    updated_at: str
    reason: str = ""
    receipt_digest: str = ""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _parse_time(value: str | datetime, name: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _record(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"], task_id=row["task_id"], operation_id=row["operation_id"],
        requested_action=row["requested_action"], request_fingerprint=row["request_fingerprint"],
        artifact_digest=row["artifact_digest"], evidence_digest=row["evidence_digest"],
        policy_digest=row["policy_digest"], requester_id=row["requester_id"],
        required_role=row["required_role"], scope=row["scope"], expires_at=row["expires_at"],
        required_approvals=int(row["required_approvals"]),
        separation_of_duties=bool(row["separation_of_duties"]), status=row["status"],
        version=int(row["version"]), created_at=row["created_at"], updated_at=row["updated_at"],
        reason=row["reason"] or "", receipt_digest=row["receipt_digest"] or "",
    )


class DurableApprovalStore:
    """Persistent approval gate with immutable bindings, CAS transitions and audit.

    Pass the live ExecutionController to share its SQLite task database and canonical
    progress journal. Identity, roles and scope must come from a trusted host adapter.
    The approval verifier fails closed unless current artifact/evidence/policy digests
    can be recomputed by the injected context provider.
    """

    def __init__(
        self,
        controller_or_path: Any,
        *,
        approver_authorizer: Callable[[str, str, str, tuple[str, ...]], bool] | None = None,
        cancellation_authorizer: Callable[[str, ApprovalRecord], bool] | None = None,
        context_provider: Callable[[ActionRequest], Mapping[str, str]] | None = None,
        reconciliation_verifier: Callable[[ApprovalRecord, str, str], bool] | None = None,
        wake_callback: Callable[[str, str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.controller = controller_or_path if hasattr(controller_or_path, "db_path") else None
        self.database_path = Path(controller_or_path.db_path if self.controller else controller_or_path)
        self.progress_journal = getattr(self.controller, "progress_journal", None)
        self.approver_authorizer = approver_authorizer
        self.cancellation_authorizer = cancellation_authorizer
        self.context_provider = context_provider
        self.reconciliation_verifier = reconciliation_verifier
        self.wake_callback = wake_callback
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS durable_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    requested_action TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_digest TEXT NOT NULL DEFAULT '',
                    requester_id TEXT NOT NULL,
                    required_role TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    required_approvals INTEGER NOT NULL,
                    separation_of_duties INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'pending','approved','rejected','expired','cancelled','superseded',
                        'executing','completed','failed','outcome_unknown'
                    )),
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    receipt_digest TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS durable_approvals_task_operation
                    ON durable_approvals(task_id, operation_id, created_at);
                CREATE TABLE IF NOT EXISTS durable_approval_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL,
                    approver_id TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN ('approve','reject')),
                    roles_json TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    decision_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(approval_id, approver_id)
                );
                CREATE TABLE IF NOT EXISTS durable_approval_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(durable_approvals)").fetchall()}
            if "checkpoint_json" not in columns:
                db.execute("ALTER TABLE durable_approvals ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '{}'")
            if "checkpoint_digest" not in columns:
                db.execute("ALTER TABLE durable_approvals ADD COLUMN checkpoint_digest TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _connect(self):
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

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _append_event(
        self, db: sqlite3.Connection, *, approval_id: str, event_type: str, actor: str,
        from_state: str, to_state: str, version: int, details: Mapping[str, Any] | None = None,
    ) -> None:
        previous = db.execute(
            "SELECT event_digest FROM durable_approval_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_digest = previous["event_digest"] if previous else "0" * 64
        created_at = _time_text(self._now())
        body = {
            "approval_id": approval_id, "event_type": event_type, "actor": actor,
            "from_state": from_state, "to_state": to_state, "version": version,
            "details": dict(details or {}), "created_at": created_at,
        }
        encoded = _canonical(body)
        event_digest = hashlib.sha256((previous_digest + encoded).encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO durable_approval_events "
            "(approval_id,event_type,actor,event_json,previous_digest,event_digest,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (approval_id, event_type, actor, encoded, previous_digest, event_digest, created_at),
        )

    def _transition(
        self, db: sqlite3.Connection, row: sqlite3.Row, to_state: str, *,
        event_type: str, actor: str, reason: str = "", receipt_digest: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> sqlite3.Row:
        old_state = row["status"]
        if to_state not in _ALLOWED_TRANSITIONS.get(old_state, frozenset()):
            raise InvalidApprovalTransition(f"cannot transition approval from {old_state} to {to_state}")
        now = _time_text(self._now())
        version = int(row["version"]) + 1
        cursor = db.execute(
            "UPDATE durable_approvals SET status=?,version=?,updated_at=?,reason=?,receipt_digest=? "
            "WHERE approval_id=? AND version=? AND status=?",
            (to_state, version, now, reason[:240], receipt_digest, row["approval_id"], row["version"], old_state),
        )
        if cursor.rowcount != 1:
            raise InvalidApprovalTransition("approval changed concurrently; reload before retrying")
        self._append_event(
            db, approval_id=row["approval_id"], event_type=event_type, actor=actor,
            from_state=old_state, to_state=to_state, version=version,
            details={**dict(details or {}), "reason": reason[:240]},
        )
        return db.execute(
            "SELECT * FROM durable_approvals WHERE approval_id=?", (row["approval_id"],)
        ).fetchone()

    def _expire(self, db: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        if row["status"] in {"pending", "approved"} and _parse_time(row["expires_at"], "expires_at") <= self._now():
            return self._transition(
                db, row, "expired", event_type="approval_expired", actor="system",
                reason="approval deadline elapsed before execution",
            )
        return row

    @staticmethod
    def _validate_digest(value: str, name: str) -> str:
        value = _required(value, name).lower()
        if not _DIGEST.fullmatch(value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        return value

    def create_request(
        self, request: ActionRequest, *, operation_id: str, artifact_digest: str,
        evidence_digest: str, policy_digest: str, requester_id: str, required_role: str,
        scope: str, expires_at: str | datetime, required_approvals: int = 1,
        separation_of_duties: bool = True, checkpoint: Mapping[str, Any] | None = None,
    ) -> ApprovalRecord:
        if not isinstance(request, ActionRequest):
            raise TypeError("request must be an ActionRequest")
        request.validate()
        task_id = _required(request.task_id, "request.task_id")
        operation_id = _required(operation_id, "operation_id")
        requester_id = _required(requester_id, "requester_id")
        required_role = _required(required_role, "required_role")
        scope = _required(scope, "scope")
        if type(required_approvals) is not int or not 1 <= required_approvals <= 5:
            raise ValueError("required_approvals must be an integer from 1 to 5")
        if type(separation_of_duties) is not bool:
            raise ValueError("separation_of_duties must be boolean")
        artifact_digest = self._validate_digest(artifact_digest, "artifact_digest")
        evidence_digest = self._validate_digest(evidence_digest, "evidence_digest")
        policy_digest = self._validate_digest(policy_digest, "policy_digest")
        expires = _parse_time(expires_at, "expires_at")
        now = self._now()
        if expires <= now:
            raise ValueError("expires_at must be in the future")
        fingerprint = request.fingerprint()
        if checkpoint is None:
            checkpoint = {
                "operation_id": operation_id, "artifact_digest": artifact_digest,
                "evidence_digest": evidence_digest, "policy_digest": policy_digest,
                "request_fingerprint": fingerprint,
            }
        checkpoint = ApprovalPending("checkpoint-validation", checkpoint).checkpoint
        for key, expected in (
            ("artifact_digest", artifact_digest), ("evidence_digest", evidence_digest),
            ("policy_digest", policy_digest), ("request_fingerprint", fingerprint),
        ):
            if key in checkpoint and checkpoint[key] != expected:
                raise ValueError(f"checkpoint {key} does not match the approval binding")
        checkpoint_json = _canonical(checkpoint)
        checkpoint_digest = _digest(checkpoint)
        identity = {
            "task_id": task_id, "operation_id": operation_id,
            "request_fingerprint": fingerprint, "artifact_digest": artifact_digest,
            "evidence_digest": evidence_digest, "policy_digest": policy_digest,
            "requester_id": requester_id, "required_role": required_role, "scope": scope,
            "expires_at": _time_text(expires), "required_approvals": required_approvals,
            "separation_of_duties": separation_of_duties,
            "checkpoint_digest": checkpoint_digest,
        }
        approval_id = "approval-" + _digest(identity)[:40]
        created_new = False
        superseded_records: list[ApprovalRecord] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            same = db.execute(
                "SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if same is not None:
                same = self._expire(db, same)
                db.commit()
                result = _record(same)
            else:
                prior_rows = db.execute(
                    "SELECT * FROM durable_approvals WHERE task_id=? AND operation_id=? "
                    "ORDER BY created_at DESC", (task_id, operation_id),
                ).fetchall()
                for prior in prior_rows:
                    prior = self._expire(db, prior)
                    if prior["status"] in {"executing", "outcome_unknown", "completed"}:
                        db.rollback()
                        raise ApprovalConflict(
                            "operation is executing, unresolved, or completed; use a new operation_id for changed content"
                        )
                    if prior["status"] in {"pending", "approved"}:
                        prior = self._transition(
                            db, prior, "superseded", event_type="approval_superseded",
                            actor="system", reason="a new request changed the approved binding",
                        )
                        superseded_records.append(_record(prior))
                created_at = _time_text(now)
                db.execute(
                    "INSERT INTO durable_approvals (approval_id,task_id,operation_id,requested_action,"
                    "request_fingerprint,artifact_digest,evidence_digest,policy_digest,checkpoint_json,checkpoint_digest,"
                    "requester_id,required_role,scope,expires_at,required_approvals,separation_of_duties,status,version,"
                    "created_at,updated_at,reason,receipt_digest) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?, '', '')",
                    (approval_id, task_id, operation_id, request.action, fingerprint, artifact_digest,
                     evidence_digest, policy_digest, checkpoint_json, checkpoint_digest, requester_id,
                     required_role, scope, _time_text(expires), required_approvals,
                     int(separation_of_duties), "pending", created_at, created_at),
                )
                self._append_event(
                    db, approval_id=approval_id, event_type="approval_requested", actor=requester_id,
                    from_state="", to_state="pending", version=1,
                    details={"operation_id": operation_id, "request_fingerprint": fingerprint,
                             "artifact_digest": artifact_digest, "evidence_digest": evidence_digest,
                             "policy_digest": policy_digest, "required_role": required_role,
                             "scope": scope, "required_approvals": required_approvals},
                )
                row = db.execute(
                    "SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)
                ).fetchone()
                db.commit()
                result = _record(row)
                created_new = True
        for prior_record in superseded_records:
            self._project_progress(prior_record, "approval_superseded")
        self._project_progress(result, "approval_requested" if created_new else "approval_state")
        return result

    def _project_progress(self, record: ApprovalRecord, event_type: str) -> None:
        journal = self.progress_journal
        if journal is None:
            return
        token = f"[approval:{record.approval_id}:v{record.version}]"
        rows = journal._events()
        if any(row.get("task_id") == record.task_id and token in row.get("message", "") for row in rows):
            return
        if record.status in {"pending", "rejected", "expired", "cancelled", "superseded", "failed", "outcome_unknown"}:
            state = "blocked"
        elif record.status == "approved":
            state = "queued"
        else:
            state = "running"
        message = f"{token} {record.status}: {record.reason or event_type}"
        journal.record(
            record.task_id, "approval_state_transition", state=state, stage="approval",
            message=message[:480],
        )

    def reconcile_progress(self) -> int:
        """Repair the latest missing journal projection without replaying old states."""
        if self.progress_journal is None:
            return 0
        with self._connect() as db:
            rows = db.execute("SELECT * FROM durable_approvals ORDER BY created_at,approval_id").fetchall()
        count = 0
        for row in rows:
            before = len(self.progress_journal._events())
            self._project_progress(_record(row), "approval_reconciliation")
            after = len(self.progress_journal._events())
            count += max(0, after - before)
        return count

    def checkpoint_for(self, approval_id: str) -> Mapping[str, Any]:
        """Load the exact bounded continuation checkpoint saved with the approval."""
        approval_id = _required(approval_id, "approval_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT checkpoint_json,checkpoint_digest FROM durable_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise KeyError("unknown approval_id")
        checkpoint = json.loads(row["checkpoint_json"])
        if not isinstance(checkpoint, Mapping) or _digest(checkpoint) != row["checkpoint_digest"]:
            raise ApprovalConflict("durable approval checkpoint digest mismatch")
        return dict(checkpoint)

    def get(self, approval_id: str) -> ApprovalRecord:
        approval_id = _required(approval_id, "approval_id")
        with self._connect() as db:
            row = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError("unknown approval_id")
        return _record(row)

    def events(self, approval_id: str | None = None) -> tuple[Mapping[str, Any], ...]:
        with self._connect() as db:
            if approval_id is None:
                rows = db.execute("SELECT * FROM durable_approval_events ORDER BY sequence").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM durable_approval_events WHERE approval_id=? ORDER BY sequence",
                    (approval_id,),
                ).fetchall()
        return tuple({
            "sequence": int(row["sequence"]), "approval_id": row["approval_id"],
            "event_type": row["event_type"], "actor": row["actor"],
            "event": json.loads(row["event_json"]), "previous_digest": row["previous_digest"],
            "event_digest": row["event_digest"], "created_at": row["created_at"],
        } for row in rows)

    def verify_audit_chain(self) -> bool:
        previous = "0" * 64
        recorded_decision_digests: set[str] = set()
        with self._connect() as db:
            rows = db.execute("SELECT event_json,previous_digest,event_digest FROM durable_approval_events ORDER BY sequence").fetchall()
            decisions = db.execute("SELECT * FROM durable_approval_decisions ORDER BY decision_id").fetchall()
        for row in rows:
            encoded = row["event_json"]
            expected = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
            if row["previous_digest"] != previous or row["event_digest"] != expected:
                return False
            body = json.loads(encoded)
            details = body.get("details", {})
            if isinstance(details, Mapping) and isinstance(details.get("decision_digest"), str):
                recorded_decision_digests.add(details["decision_digest"])
            previous = expected
        for decision in decisions:
            vote_body = {
                "approval_id": decision["approval_id"], "approver_id": decision["approver_id"],
                "decision": decision["decision"], "roles": json.loads(decision["roles_json"]),
                "scope": decision["scope"], "reason": decision["reason"],
                "created_at": decision["created_at"],
            }
            digest = _digest(vote_body)
            if digest != decision["decision_digest"] or digest not in recorded_decision_digests:
                return False
        return True

    def decide(
        self, approval_id: str, decision: str, *, approver_id: str, roles: tuple[str, ...] | list[str],
        scope: str, reason: str = "", now: datetime | None = None,
    ) -> ApprovalRecord:
        approval_id = _required(approval_id, "approval_id")
        approver_id = _required(approver_id, "approver_id")
        scope = _required(scope, "scope")
        reason = reason.strip() if isinstance(reason, str) else ""
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        normalized_roles = tuple(sorted(set(_required(role, "role") for role in roles)))
        with self._connect() as db:
            first = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if first is None:
            raise KeyError("unknown approval_id")
        if scope != first["scope"] or first["required_role"] not in normalized_roles:
            raise PermissionError("approver role or scope does not match the request")
        if self.approver_authorizer is None:
            raise PermissionError("trusted approver authorization is not configured")
        try:
            authorized = bool(self.approver_authorizer(
                approver_id, first["required_role"], first["scope"], normalized_roles
            ))
        except Exception as exc:
            raise PermissionError("trusted approver authorization failed closed") from exc
        if not authorized:
            raise PermissionError("trusted approver authorization rejected the decision")
        current = _parse_time(now or self._now(), "now")
        wake = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("unknown approval_id")
            row = self._expire(db, row)
            if row["status"] == "expired":
                db.commit()
                self._project_progress(_record(row), "approval_expired")
                raise InvalidApprovalTransition("approval expired before the decision was recorded")
            prior_vote = db.execute(
                "SELECT decision FROM durable_approval_decisions WHERE approval_id=? AND approver_id=?",
                (approval_id, approver_id),
            ).fetchone()
            if prior_vote is not None:
                if prior_vote["decision"] != decision:
                    db.rollback()
                    raise InvalidApprovalTransition("approver cannot change a recorded decision")
                db.commit()
                result = _record(row)
                wake = result.status == "approved"
            else:
                if row["status"] != "pending":
                    db.rollback()
                    raise InvalidApprovalTransition(f"approval is {row['status']}, not pending")
                if decision == "approve" and row["separation_of_duties"] and approver_id == row["requester_id"]:
                    db.rollback()
                    raise PermissionError("requester cannot approve their own operation")
                created_at = _time_text(current)
                vote_body = {
                    "approval_id": approval_id, "approver_id": approver_id, "decision": decision,
                    "roles": list(normalized_roles), "scope": scope, "reason": reason[:240],
                    "created_at": created_at,
                }
                db.execute(
                    "INSERT INTO durable_approval_decisions "
                    "(approval_id,approver_id,decision,roles_json,scope,reason,decision_digest,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (approval_id, approver_id, decision, _canonical(list(normalized_roles)), scope,
                     reason[:240], _digest(vote_body), created_at),
                )
                if decision == "reject":
                    row = self._transition(
                        db, row, "rejected", event_type="approval_rejected", actor=approver_id,
                        reason=reason or "approval rejected",
                        details={"decision_digest": _digest(vote_body), "decision": decision, "scope": scope},
                    )
                else:
                    count = int(db.execute(
                        "SELECT COUNT(*) FROM durable_approval_decisions WHERE approval_id=? AND decision='approve'",
                        (approval_id,),
                    ).fetchone()[0])
                    target = "approved" if count >= int(row["required_approvals"]) else "pending"
                    row = self._transition(
                        db, row, target, event_type="approval_vote_recorded", actor=approver_id,
                        reason=reason or ("approval threshold reached" if target == "approved" else "additional approver required"),
                        details={"approval_count": count, "required_approvals": int(row["required_approvals"]),
                                 "decision_digest": _digest(vote_body), "decision": decision, "scope": scope},
                    )
                db.commit()
                result = _record(row)
                wake = result.status == "approved"
        self._project_progress(result, "approval_decision")
        if wake and self.wake_callback is not None:
            try:
                self.wake_callback(result.task_id, result.approval_id)
            except Exception as exc:
                raise RuntimeError("approval is durable but worker wake-up failed; scheduler recovery is required") from exc
        return result

    def _assert_binding(
        self, record: ApprovalRecord, request: ActionRequest, *,
        artifact_digest: str, evidence_digest: str, policy_digest: str,
    ) -> None:
        if request.fingerprint() != record.request_fingerprint or request.task_id != record.task_id:
            raise ApprovalConflict("request identity changed after approval")
        if (
            self._validate_digest(artifact_digest, "artifact_digest") != record.artifact_digest
            or self._validate_digest(evidence_digest, "evidence_digest") != record.evidence_digest
            or self._validate_digest(policy_digest, "policy_digest") != record.policy_digest
        ):
            raise ApprovalConflict("artifact, evidence, or policy changed after approval")

    def claim_execution(
        self, approval_id: str, request: ActionRequest, *, artifact_digest: str,
        evidence_digest: str, policy_digest: str,
    ) -> ApprovalReceipt:
        if not isinstance(request, ActionRequest):
            raise TypeError("request must be an ActionRequest")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("unknown approval_id")
            row = self._expire(db, row)
            if row["status"] == "expired":
                db.commit()
                self._project_progress(_record(row), "approval_expired")
                raise InvalidApprovalTransition("approval expired before execution")
            if row["status"] != "approved":
                db.rollback()
                raise InvalidApprovalTransition(f"approval is {row['status']}, not approved")
            try:
                self._assert_binding(_record(row), request, artifact_digest=artifact_digest,
                                     evidence_digest=evidence_digest, policy_digest=policy_digest)
            except ApprovalConflict as exc:
                row = self._transition(
                    db, row, "superseded", event_type="approval_binding_invalidated", actor="system",
                    reason=str(exc),
                )
                db.commit()
                self._project_progress(_record(row), "approval_invalidated")
                raise
            row = self._transition(
                db, row, "executing", event_type="approved_operation_claimed", actor="runtime",
                reason="approval binding revalidated before dispatch",
            )
            approvers = db.execute(
                "SELECT approver_id FROM durable_approval_decisions WHERE approval_id=? AND decision='approve' ORDER BY approver_id",
                (approval_id,),
            ).fetchall()
            db.commit()
            result = _record(row)
        self._project_progress(result, "operation_claimed")
        return ApprovalReceipt(
            approval_id=approval_id, approver=_canonical([item["approver_id"] for item in approvers]),
            request_fingerprint=result.request_fingerprint,
        )

    def verify_receipt(self, receipt: ApprovalReceipt, request: ActionRequest) -> bool:
        """Use as ActionGovernanceGateway.approval_verifier; current context is mandatory."""
        if self.context_provider is None:
            return False
        try:
            record = self.get(receipt.approval_id)
            if record.status != "executing" or request.fingerprint() != record.request_fingerprint:
                return False
            if request.task_id != record.task_id:
                return False
            context = self.context_provider(request)
            if not isinstance(context, Mapping):
                return False
            if (
                self._validate_digest(context.get("artifact_digest"), "artifact_digest") != record.artifact_digest
                or self._validate_digest(context.get("evidence_digest"), "evidence_digest") != record.evidence_digest
                or self._validate_digest(context.get("policy_digest"), "policy_digest") != record.policy_digest
            ):
                return False
            with self._connect() as db:
                rows = db.execute(
                    "SELECT approver_id FROM durable_approval_decisions WHERE approval_id=? AND decision='approve' ORDER BY approver_id",
                    (receipt.approval_id,),
                ).fetchall()
            return (
                receipt.request_fingerprint == record.request_fingerprint
                and receipt.approver == _canonical([item["approver_id"] for item in rows])
                and bool(rows)
            )
        except Exception:
            return False

    def execute_approved_action(
        self, approval_id: str, request: ActionRequest, gateway: Any, handler: Callable[[], Any], *,
        artifact_digest: str, evidence_digest: str, policy_digest: str,
    ) -> Any:
        """Claim once, invoke the existing governance gateway, and record truthful outcome."""
        if not callable(getattr(gateway, "requires_approval", None)) or not gateway.requires_approval(request):
            raise PermissionError("the action gateway policy does not require approval for this request")
        if not callable(getattr(gateway, "is_external_effect", None)) or not gateway.is_external_effect(request):
            raise PermissionError("approval-gated execution requires an external effect protected by the durable ledger")
        if not getattr(gateway, "has_durable_side_effect_ledger", False):
            raise PermissionError("approval-gated execution requires the durable side-effect ledger")
        if gateway.durable_side_effect_preflight(request) is not None:
            raise PermissionError("action gateway rejected the durable side-effect preflight")
        receipt = self.claim_execution(
            approval_id, request, artifact_digest=artifact_digest,
            evidence_digest=evidence_digest, policy_digest=policy_digest,
        )
        if not self.verify_receipt(receipt, request):
            self.mark_outcome(
                approval_id, "failed", reason="approved binding changed or could not be revalidated before dispatch",
            )
            raise PermissionError("approval binding could not be revalidated before dispatch")
        try:
            result = gateway.execute(request, handler, approval=receipt)
        except BaseException as exc:
            self.mark_outcome(
                approval_id, "outcome_unknown", reason=f"gateway raised {type(exc).__name__}; remote result may be unknown",
            )
            raise
        if getattr(result, "executed", False) or getattr(result, "replayed", False):
            self.mark_outcome(
                approval_id, "completed", reason="governance gateway returned a durable action result",
                receipt_digest=str(getattr(result, "result_digest", "")),
            )
        else:
            self.mark_outcome(
                approval_id, "failed", reason=str(getattr(getattr(result, "verdict", None), "reason", "action was denied")),
            )
        return result

    def mark_outcome(
        self, approval_id: str, outcome: str, *, reason: str = "", receipt_digest: str = "",
    ) -> ApprovalRecord:
        if outcome not in {"completed", "failed", "outcome_unknown"}:
            raise ValueError("outcome must be completed, failed, or outcome_unknown")
        if receipt_digest and not _DIGEST.fullmatch(receipt_digest):
            raise ValueError("receipt_digest must be a SHA-256 digest")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("unknown approval_id")
            row = self._transition(
                db, row, outcome, event_type=f"operation_{outcome}", actor="runtime",
                reason=reason or outcome, receipt_digest=receipt_digest,
            )
            db.commit()
            result = _record(row)
        self._project_progress(result, "operation_outcome")
        return result

    def reconcile_outcome(self, approval_id: str, *, outcome: str, evidence_ref: str) -> ApprovalRecord:
        """Close an unknown outcome only after a trusted host verifier accepts provider evidence.

        Reconcile DurableSideEffectLedger first. This method updates the approval lifecycle;
        it does not replace the canonical side-effect receipt or authorize redispatch.
        """
        if outcome not in {"completed", "failed"}:
            raise ValueError("reconciled outcome must be completed or failed")
        evidence_ref = _required(evidence_ref, "evidence_ref")
        record = self.get(approval_id)
        if record.status != "outcome_unknown":
            raise InvalidApprovalTransition("only outcome_unknown approvals can be reconciled")
        if self.reconciliation_verifier is None:
            raise PermissionError("trusted outcome reconciliation verifier is required")
        try:
            verified = bool(self.reconciliation_verifier(record, outcome, evidence_ref))
        except Exception as exc:
            raise PermissionError("outcome reconciliation failed closed") from exc
        if not verified:
            raise PermissionError("provider outcome evidence was rejected")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None or row["status"] != "outcome_unknown":
                db.rollback()
                raise InvalidApprovalTransition("approval outcome changed during reconciliation")
            row = self._transition(
                db, row, outcome, event_type="operation_outcome_reconciled", actor="reconciler",
                reason="trusted provider evidence reconciled the unknown outcome",
                receipt_digest=_digest({"evidence_ref": evidence_ref, "outcome": outcome}),
                details={"evidence_ref_digest": _digest(evidence_ref), "outcome": outcome},
            )
            db.commit()
            result = _record(row)
        self._project_progress(result, "outcome_reconciled")
        return result

    def cancel(self, approval_id: str, *, actor: str, reason: str) -> ApprovalRecord:
        """Cancel a pending/approved request only after trusted host authorization."""
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        record = self.get(approval_id)
        if self.cancellation_authorizer is None:
            raise PermissionError("trusted cancellation authorizer is required")
        try:
            authorized = self.cancellation_authorizer(actor, record)
        except Exception as exc:
            raise PermissionError("cancellation authorization failed closed") from exc
        if authorized is not True:
            raise PermissionError("actor is not authorized to cancel this approval")

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM durable_approvals WHERE approval_id=?", (approval_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError("unknown approval_id")
            row = self._expire(db, row)
            if row["status"] == "expired":
                db.commit()
                result = _record(row)
                event_type = "approval_expired"
            else:
                row = self._transition(
                    db, row, "cancelled", event_type="approval_cancelled",
                    actor=actor, reason=reason,
                )
                db.commit()
                result = _record(row)
                event_type = "approval_cancelled"
        self._project_progress(result, event_type)
        return result

    def expire_due(self, *, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        changed: list[ApprovalRecord] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM durable_approvals WHERE status IN ('pending','approved') "
                "AND expires_at<=? ORDER BY expires_at LIMIT ?",
                (_time_text(self._now()), limit),
            ).fetchall()
            for row in rows:
                changed.append(_record(self._transition(
                    db, row, "expired", event_type="approval_expired", actor="system",
                    reason="approval deadline elapsed before execution",
                )))
            db.commit()
        for record in changed:
            self._project_progress(record, "approval_expired")
        return len(changed)

    def approved_for_resume(self, *, limit: int = 100) -> tuple[ApprovalRecord, ...]:
        """Return approved work for a scheduler; waiting approvals do not hold workers."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        self.expire_due(limit=limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM durable_approvals WHERE status='approved' ORDER BY updated_at,approval_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def wake_approved(self, *, limit: int = 100) -> int:
        """Recover missed notifications by signaling approved operations to the host scheduler."""
        if self.wake_callback is None:
            return 0
        records = self.approved_for_resume(limit=limit)
        for record in records:
            self.wake_callback(record.task_id, record.approval_id)
        return len(records)

    def recover_interrupted(self, *, older_than_seconds: float = 300.0) -> int:
        """Convert stale executing approvals to outcome_unknown; never redispatch them."""
        if isinstance(older_than_seconds, bool) or not isinstance(older_than_seconds, (int, float)) or not math.isfinite(older_than_seconds) or older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be finite and positive")
        cutoff = _time_text(self._now() - timedelta(seconds=float(older_than_seconds)))
        changed: list[ApprovalRecord] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            has_tasks = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone() is not None
            if has_tasks:
                rows = db.execute(
                    "SELECT a.*,t.state AS task_state,t.lease_until AS task_lease_until,"
                    "t.heartbeat_at AS task_heartbeat_at FROM durable_approvals a "
                    "LEFT JOIN tasks t ON t.id=a.task_id "
                    "WHERE a.status='executing' AND a.updated_at<=? ORDER BY a.updated_at LIMIT 1000",
                    (cutoff,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM durable_approvals WHERE status='executing' AND updated_at<=? ORDER BY updated_at LIMIT 1000",
                    (cutoff,),
                ).fetchall()
            now = self._now()
            for row in rows:
                if has_tasks and row["task_state"] == "running":
                    try:
                        lease = _parse_time(row["task_lease_until"], "task_lease_until") if row["task_lease_until"] else None
                        heartbeat = _parse_time(row["task_heartbeat_at"], "task_heartbeat_at") if row["task_heartbeat_at"] else None
                    except (TypeError, ValueError):
                        lease, heartbeat = None, None
                    if lease is not None and heartbeat is not None and lease > now and (now - heartbeat).total_seconds() <= 90:
                        continue
                base_row = db.execute(
                    "SELECT * FROM durable_approvals WHERE approval_id=?", (row["approval_id"],)
                ).fetchone()
                if base_row is None or base_row["status"] != "executing":
                    continue
                changed.append(_record(self._transition(
                    db, base_row, "outcome_unknown", event_type="interrupted_execution_detected",
                    actor="recovery", reason="execution lease expired; remote outcome requires reconciliation",
                )))
            db.commit()
        for record in changed:
            self._project_progress(record, "interrupted_execution")
        return len(changed)
