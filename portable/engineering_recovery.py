"""Evidence-driven, bounded recovery for difficult engineering tasks.

The ledger is separate from authorization and capability promotion. It records
attempts and checkpoints; callers still own execution, review, and Guard policy.
SQLite makes the recovery budget survive process restarts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


class RecoveryAction(str, Enum):
    RETRY = "retry"
    ALTERNATE_PLAN = "alternate_plan"
    BLOCKED = "blocked"


class FailureClass(str, Enum):
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    CODE_DEFECT = "code_defect"
    CONTRACT_MISMATCH = "contract_mismatch"
    MISSING_REQUIREMENT = "missing_requirement"
    PERMISSION_OR_CONFIGURATION = "permission_or_configuration"
    UNKNOWN = "unknown"


class RecoveryLimitReached(RuntimeError):
    """Raised before an attempt that would exceed the configured retry budget."""

    def __init__(self, action: RecoveryAction, reason: str) -> None:
        super().__init__(reason)
        self.action = action
        self.reason = reason


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    same_hypothesis_attempts: int
    total_attempts: int


@dataclass(frozen=True)
class RecoveryAttempt:
    attempt_id: int
    task_id: str
    phase: str
    failure_signature: str
    hypothesis: str
    failure_class: str
    action: str
    outcome: str
    evidence: tuple[str, ...]
    head_sha: str
    created_at: float


@dataclass(frozen=True)
class Checkpoint:
    task_id: str
    phase: str
    status: str
    head_sha: str
    payload: Mapping[str, Any]
    updated_at: float


@dataclass(frozen=True)
class CiGateResult:
    accepted: bool
    reason: str


def verify_ci_gate(
    *,
    expected_head_sha: str,
    ci_head_sha: str,
    status: str,
    conclusion: str | None,
    required_checks: Iterable[str],
    passed_checks: Iterable[str],
    skipped_checks: Iterable[str] = (),
) -> CiGateResult:
    """Fail closed unless successful CI proves the exact current PR head."""
    if not expected_head_sha or not ci_head_sha or ci_head_sha != expected_head_sha:
        return CiGateResult(False, "CI head SHA does not match the expected PR head")
    if status.lower() != "completed":
        return CiGateResult(False, f"CI is not complete: {status or 'unknown'}")
    if (conclusion or "").lower() != "success":
        return CiGateResult(False, f"CI conclusion is not successful: {conclusion or 'missing'}")
    required = set(required_checks)
    passed = set(passed_checks)
    skipped = set(skipped_checks)
    if required & skipped:
        return CiGateResult(False, "a required check was skipped")
    missing = sorted(required - passed)
    if missing:
        return CiGateResult(False, "required checks missing or not passing: " + ", ".join(missing))
    return CiGateResult(True, "all required checks passed on the exact PR head")


class EngineeringRecoveryLedger:
    """Durable attempt budget, phase checkpoint, and recovery decision ledger.

    The same failure signature and hypothesis is allowed at most twice. Further
    progress requires a changed hypothesis or implementation path. Total attempts
    and elapsed wall time are bounded as well. No method executes code or grants
    authority to change policy.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_same_hypothesis_attempts: int = 2,
        max_total_attempts: int = 12,
        max_elapsed_seconds: float = 3600,
    ) -> None:
        if max_same_hypothesis_attempts < 1 or max_total_attempts < 1:
            raise ValueError("attempt budgets must be positive")
        if max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds must be positive")
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_same_hypothesis_attempts = max_same_hypothesis_attempts
        self.max_total_attempts = max_total_attempts
        self.max_elapsed_seconds = float(max_elapsed_seconds)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recovery_runs(
                    task_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    intent_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_attempts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    failure_signature TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    failure_class TEXT NOT NULL DEFAULT 'unknown',
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'running',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    head_sha TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_attempt_key
                    ON recovery_attempts(task_id, phase, failure_signature, hypothesis);
                CREATE TABLE IF NOT EXISTS recovery_checkpoints(
                    task_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(task_id, phase)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(recovery_attempts)").fetchall()}
            if "failure_class" not in columns:
                db.execute("ALTER TABLE recovery_attempts ADD COLUMN failure_class TEXT NOT NULL DEFAULT 'unknown'")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _required(value: str, field: str) -> str:
        result = str(value).strip()
        if not result:
            raise ValueError(f"{field} is required")
        return result

    def start_run(self, task_id: str, intent: str) -> str:
        task_id = self._required(task_id, "task_id")
        intent = self._required(intent, "intent")
        digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()
        now = time.time()
        with self._db() as db:
            row = db.execute("SELECT intent_digest FROM recovery_runs WHERE task_id=?", (task_id,)).fetchone()
            if row and row["intent_digest"] != digest:
                raise ValueError("task_id already belongs to a different immutable intent")
            db.execute(
                "INSERT OR IGNORE INTO recovery_runs(task_id,started_at,intent_digest) VALUES(?,?,?)",
                (task_id, now, digest),
            )
        return digest

    def decision(
        self, task_id: str, phase: str, failure_signature: str, hypothesis: str
    ) -> RecoveryDecision:
        task_id, phase, failure_signature, hypothesis = tuple(
            self._required(v, n) for v, n in (
                (task_id, "task_id"), (phase, "phase"),
                (failure_signature, "failure_signature"), (hypothesis, "hypothesis")
            )
        )
        with self._db() as db:
            run = db.execute("SELECT started_at FROM recovery_runs WHERE task_id=?", (task_id,)).fetchone()
            if not run:
                return RecoveryDecision(RecoveryAction.BLOCKED, "run must be initialized before recovery", 0, 0)
            total = int(db.execute("SELECT COUNT(*) FROM recovery_attempts WHERE task_id=?", (task_id,)).fetchone()[0])
            same = int(db.execute(
                """SELECT COUNT(*) FROM recovery_attempts
                   WHERE task_id=? AND phase=? AND failure_signature=? AND hypothesis=?""",
                (task_id, phase, failure_signature, hypothesis),
            ).fetchone()[0])
        if time.time() - float(run["started_at"]) > self.max_elapsed_seconds:
            return RecoveryDecision(RecoveryAction.BLOCKED, "wall-clock recovery budget exhausted", same, total)
        if total >= self.max_total_attempts:
            return RecoveryDecision(RecoveryAction.BLOCKED, "total recovery attempt budget exhausted", same, total)
        if same >= self.max_same_hypothesis_attempts:
            return RecoveryDecision(
                RecoveryAction.ALTERNATE_PLAN,
                "same failure and hypothesis exhausted; change diagnosis or implementation path",
                same, total,
            )
        return RecoveryDecision(RecoveryAction.RETRY, "bounded attempt budget remains", same, total)

    def begin_attempt(
        self,
        task_id: str,
        phase: str,
        failure_signature: str,
        hypothesis: str,
        *,
        action: str,
        failure_class: FailureClass | str = FailureClass.UNKNOWN,
        head_sha: str = "",
    ) -> int:
        decision = self.decision(task_id, phase, failure_signature, hypothesis)
        if decision.action != RecoveryAction.RETRY:
            raise RecoveryLimitReached(decision.action, decision.reason)
        if action not in {"reproduce", "narrow_fix", "alternate_implementation", "independent_review", "rollback"}:
            raise ValueError("action must name a recognized recovery step")
        failure_class_value = failure_class.value if isinstance(failure_class, FailureClass) else str(failure_class)
        if failure_class_value not in {item.value for item in FailureClass}:
            raise ValueError("failure_class must be a recognized failure category")
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            total = int(db.execute("SELECT COUNT(*) FROM recovery_attempts WHERE task_id=?", (task_id,)).fetchone()[0])
            same = int(db.execute(
                """SELECT COUNT(*) FROM recovery_attempts
                   WHERE task_id=? AND phase=? AND failure_signature=? AND hypothesis=?""",
                (task_id, phase, failure_signature, hypothesis),
            ).fetchone()[0])
            run = db.execute("SELECT started_at FROM recovery_runs WHERE task_id=?", (task_id,)).fetchone()
            if not run or total >= self.max_total_attempts or same >= self.max_same_hypothesis_attempts or now - float(run["started_at"]) > self.max_elapsed_seconds:
                action_result = RecoveryAction.ALTERNATE_PLAN if same >= self.max_same_hypothesis_attempts else RecoveryAction.BLOCKED
                raise RecoveryLimitReached(action_result, "recovery budget changed or is exhausted")
            cur = db.execute(
                """INSERT INTO recovery_attempts
                   (task_id,phase,failure_signature,hypothesis,failure_class,action,outcome,head_sha,created_at)
                   VALUES(?,?,?,?,?,?,'running',?,?)""",
                (task_id, phase, failure_signature, hypothesis, failure_class_value, action, head_sha, now),
            )
            return int(cur.lastrowid)

    def finish_attempt(
        self, attempt_id: int, *, outcome: str, evidence: Iterable[str] = ()
    ) -> None:
        if outcome not in {"passed", "failed", "blocked", "rolled_back"}:
            raise ValueError("outcome must be passed, failed, blocked, or rolled_back")
        values = [str(item).strip() for item in evidence if str(item).strip()]
        with self._db() as db:
            cur = db.execute(
                "UPDATE recovery_attempts SET outcome=?, evidence_json=? WHERE id=? AND outcome='running'",
                (outcome, json.dumps(values, sort_keys=True), int(attempt_id)),
            )
            if cur.rowcount != 1:
                raise KeyError(f"running recovery attempt not found: {attempt_id}")

    def checkpoint(
        self, task_id: str, phase: str, *, status: str, head_sha: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        task_id = self._required(task_id, "task_id")
        phase = self._required(phase, "phase")
        if status not in {"pending", "in_progress", "verified", "blocked", "rolled_back"}:
            raise ValueError("invalid checkpoint status")
        data = dict(payload or {})
        if status == "verified":
            if not head_sha.strip() or data.get("verified_head_sha") != head_sha.strip():
                raise ValueError("verified checkpoints must bind evidence to the exact head SHA")
            if not data.get("verification_evidence"):
                raise ValueError("verified checkpoints require non-empty verification_evidence")
        now = time.time()
        with self._db() as db:
            if not db.execute("SELECT 1 FROM recovery_runs WHERE task_id=?", (task_id,)).fetchone():
                raise KeyError(f"unknown recovery run: {task_id}")
            db.execute(
                """INSERT INTO recovery_checkpoints(task_id,phase,status,head_sha,payload_json,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(task_id,phase) DO UPDATE SET
                   status=excluded.status,head_sha=excluded.head_sha,
                   payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (task_id, phase, status, head_sha.strip(), json.dumps(data, sort_keys=True), now),
            )
        return Checkpoint(task_id, phase, status, head_sha.strip(), data, now)

    def get_checkpoint(self, task_id: str, phase: str) -> Checkpoint | None:
        with self._db() as db:
            row = db.execute(
                "SELECT task_id,phase,status,head_sha,payload_json,updated_at FROM recovery_checkpoints WHERE task_id=? AND phase=?",
                (task_id, phase),
            ).fetchone()
        if not row:
            return None
        return Checkpoint(row["task_id"], row["phase"], row["status"], row["head_sha"],
                          json.loads(row["payload_json"]), float(row["updated_at"]))

    def attempts(self, task_id: str) -> tuple[RecoveryAttempt, ...]:
        with self._db() as db:
            rows = db.execute(
                """SELECT id,task_id,phase,failure_signature,hypothesis,failure_class,action,outcome,
                          evidence_json,head_sha,created_at FROM recovery_attempts
                   WHERE task_id=? ORDER BY id""", (task_id,)
            ).fetchall()
        return tuple(RecoveryAttempt(
            int(r["id"]), r["task_id"], r["phase"], r["failure_signature"], r["hypothesis"],
            r["failure_class"], r["action"], r["outcome"], tuple(json.loads(r["evidence_json"])),
            r["head_sha"], float(r["created_at"])
        ) for r in rows)
