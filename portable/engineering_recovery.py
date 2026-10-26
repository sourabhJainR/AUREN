"""Evidence-driven, bounded recovery for difficult engineering tasks.

The ledger is separate from authorization and capability promotion. It records
attempts and checkpoints; callers still own execution, review, and Guard policy.
SQLite makes the recovery budget survive process restarts.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


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

    @contextmanager
    def _db(self):
        """Yield a short-lived connection and always close it after the transaction.

        sqlite3.Connection's context manager commits/rolls back but does not close
        the connection. Explicit closure is important for WAL cleanup and Windows
        file replacement/removal during recovery and tests.
        """
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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
        decision_receipt: Mapping[str, Any] | None = None,
        decision_journal: Any | None = None,
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
            phase_total = int(db.execute(
                "SELECT COUNT(*) FROM recovery_attempts WHERE task_id=? AND phase=?",
                (task_id, phase),
            ).fetchone()[0])
            if decision_receipt is not None:
                if not isinstance(decision_receipt, Mapping):
                    raise TypeError("decision_receipt must be a mapping")
                if (
                    decision_receipt.get("work_id") != task_id
                    or decision_receipt.get("phase") != phase
                    or decision_receipt.get("attempt_number") != phase_total + 1
                ):
                    raise ValueError("decision receipt key does not match the next ledger attempt")
                from .recovery_decision_journal import RecoveryDecisionJournal
                writer = decision_journal or RecoveryDecisionJournal
                writer.record_in_transaction(db, **dict(decision_receipt))
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


class GraphCheckpointMismatch(RuntimeError):
    """The durable graph journal and canonical recovery checkpoint disagree."""


_GRAPH_STATUSES = frozenset({"waiting", "working", "adopting", "blocked", "failed", "unknown", "cancelled", "complete"})
_TRANSITIONS = {
    "waiting": frozenset({"working", "blocked", "cancelled"}),
    "working": frozenset({"adopting", "blocked", "failed", "unknown", "cancelled", "complete"}),
    "adopting": frozenset({"blocked", "failed", "unknown", "cancelled", "complete"}),
    "blocked": frozenset({"waiting", "cancelled"}),
    "failed": frozenset({"waiting", "cancelled"}),
    "unknown": frozenset({"blocked", "failed", "cancelled"}),
    "cancelled": frozenset(),
    "complete": frozenset(),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_budget_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise TypeError("budget_snapshot must be a mapping from the canonical RuntimeExecutionGuard")
    safe = json.loads(_canonical(dict(snapshot)))
    supplied = safe.pop("digest", None)
    if not isinstance(supplied, str) or supplied != _digest(safe):
        raise ValueError("runtime budget snapshot digest mismatch")
    safe["digest"] = supplied
    for field in ("max_steps", "steps_used", "max_attempts", "attempts_used"):
        if type(safe.get(field)) is not int or safe[field] < 0:
            raise ValueError(f"runtime budget snapshot has invalid {field}")
    if safe["steps_used"] > safe["max_steps"] or safe["attempts_used"] > safe["max_attempts"]:
        raise ValueError("runtime budget usage exceeds its canonical ceiling")
    return safe


def _assert_budget_monotonic(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for ceiling in ("max_steps", "max_attempts"):
        if previous.get(ceiling) != current.get(ceiling):
            raise ValueError(f"runtime budget ceiling changed during graph execution: {ceiling}")
    for used in ("steps_used", "attempts_used"):
        if current[used] < previous[used]:
            raise ValueError(f"runtime budget was refunded during graph execution: {used}")
    if previous.get("cancelled") is True and current.get("cancelled") is not True:
        raise ValueError("cancelled runtime budget cannot be revived")
    if previous.get("deadline_unix") is not None and current.get("deadline_unix") is not None:
        if current["deadline_unix"] > previous["deadline_unix"] + 1.0:
            raise ValueError("runtime deadline was extended during graph execution")
    if previous.get("remaining_seconds") is not None and current.get("remaining_seconds") is not None:
        if current["remaining_seconds"] > previous["remaining_seconds"] + 1.0:
            raise ValueError("runtime remaining time increased during graph execution")
    previous_charged = set(previous.get("charged_operations", ()))
    current_charged = set(current.get("charged_operations", ()))
    if not previous_charged.issubset(current_charged):
        raise ValueError("charged operations disappeared from runtime budget snapshot")
    previous_failures = previous.get("failure_counts", {})
    current_failures = current.get("failure_counts", {})
    for key, count in previous_failures.items():
        if int(current_failures.get(key, -1)) < int(count):
            raise ValueError("runtime failure counters decreased during graph execution")


class GraphRunRecoveryBridge:
    """Bind graph node snapshots to the existing hash-chained journal and recovery ledger."""

    PHASE = "graph_execution"

    def __init__(
        self,
        *,
        run_id: str,
        plan: Any,
        intent_digest: str,
        source_revision: str,
        ledger: EngineeringRecoveryLedger,
        journal: Any,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if not isinstance(intent_digest, str) or not _SHA256.fullmatch(intent_digest):
            raise ValueError("intent_digest must be a lowercase SHA-256 digest")
        if not isinstance(source_revision, str) or not _REVISION.fullmatch(source_revision):
            raise ValueError("source_revision must be a full lowercase commit revision")
        from .agency_evolution_gates import ImprovementJournal
        from .task_planner import TaskPlan
        if not isinstance(ledger, EngineeringRecoveryLedger) or not isinstance(journal, ImprovementJournal):
            raise TypeError("reuse the canonical EngineeringRecoveryLedger and ImprovementJournal")
        if not isinstance(plan, TaskPlan):
            raise TypeError("plan must be the canonical TaskPlan")
        self.run_id = run_id.strip()
        self.plan = plan
        self.plan_digest = self._current_plan_digest()
        self.intent_digest = intent_digest
        self.source_revision = source_revision
        self.ledger = ledger
        self.journal = journal
        self._immutable_intent = f"graph-run:{self.plan_digest}:{self.intent_digest}:{self.source_revision}"
        self._dependencies = {
            task_id: tuple(sorted(task.dependencies))
            for task_id, task in plan.tasks.items()
        }

    def _current_plan_digest(self) -> str:
        return self.plan.digest()

    def _initial_snapshot(self, budget_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        budget = _validate_budget_snapshot(budget_snapshot)
        return {
            "schema": "auren-graph-run-checkpoint-v1",
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "intent_digest": self.intent_digest,
            "source_revision": self.source_revision,
            "run_status": "in_progress",
            "next_safe_action": "schedule_dependency_ready_node",
            "budget_snapshot": budget,
            "nodes": {
                node_id: {
                    "status": "waiting",
                    "dependencies": list(dependencies),
                    "blockers": [],
                    "worker_id": None,
                    "workspace_id": None,
                    "attempts": 0,
                    "budget_snapshot": budget,
                    "evidence_refs": [],
                    "next_safe_action": "wait_for_dependencies_or_schedule",
                }
                for node_id, dependencies in sorted(self._dependencies.items())
            },
        }

    def _events(self):
        return tuple(event for event in self.journal.read() if event.work_id == self.run_id and event.state == "graph_checkpoint")

    def _checkpoint_payload(self, event, snapshot: Mapping[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        payload = {
            "contract": "auren-graph-run-ledger-pointer-v1",
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "intent_digest": self.intent_digest,
            "source_revision": self.source_revision,
            "journal_event_id": event.event_id,
            "journal_event_digest": event.digest,
            "snapshot_digest": _digest(snapshot),
            "run_status": snapshot["run_status"],
            "next_safe_action": snapshot["next_safe_action"],
            "node_count": len(snapshot["nodes"]),
        }
        if snapshot["run_status"] == "complete":
            evidence = sorted({ref for node in snapshot["nodes"].values() for ref in node["evidence_refs"]})
            if not evidence or any(node["status"] != "complete" for node in snapshot["nodes"].values()):
                raise GraphCheckpointMismatch("complete graph run is missing required node evidence")
            payload["verified_head_sha"] = self.source_revision
            payload["verification_evidence"] = evidence
        if reason:
            payload["reconciliation_reason"] = reason.strip()
        return payload

    def _publish_pointer(self, event, snapshot: Mapping[str, Any], *, reason: str | None = None):
        payload = self._checkpoint_payload(event, snapshot, reason=reason)
        if snapshot["run_status"] == "complete":
            status = "verified"
        elif snapshot["run_status"] in {"blocked", "cancelled", "failed"}:
            status = "blocked"
        else:
            status = "in_progress"
        return self.ledger.checkpoint(
            self.run_id, self.PHASE, status=status, head_sha=self.source_revision, payload=payload,
        )

    def start(self, *, budget_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        self.ledger.start_run(self.run_id, self._immutable_intent)
        checkpoint = self.ledger.get_checkpoint(self.run_id, self.PHASE)
        events = self._events()
        if checkpoint is not None:
            return self.load()
        if events:
            raise GraphCheckpointMismatch("journal contains graph progress but its ledger pointer is missing")
        snapshot = self._initial_snapshot(budget_snapshot)
        event = self.journal.append(
            event_id=f"graph:{self.run_id}:start", work_id=self.run_id,
            state="graph_checkpoint",
            payload={"snapshot": snapshot, "snapshot_digest": _digest(snapshot), "event_kind": "start"},
        )
        self._publish_pointer(event, snapshot)
        return snapshot

    def load(self) -> dict[str, Any]:
        if self._current_plan_digest() != self.plan_digest:
            raise GraphCheckpointMismatch("canonical TaskPlan changed after graph checkpoint binding")
        checkpoint = self.ledger.get_checkpoint(self.run_id, self.PHASE)
        if checkpoint is None:
            raise GraphCheckpointMismatch("canonical graph recovery checkpoint is missing")
        events = self._events()
        if not events:
            raise GraphCheckpointMismatch("graph journal has no checkpoint event")
        latest = events[-1]
        if checkpoint.payload.get("journal_event_id") != latest.event_id or checkpoint.payload.get("journal_event_digest") != latest.digest:
            raise GraphCheckpointMismatch("graph journal advanced without a matching ledger checkpoint; explicit reconciliation required")
        event_payload = latest.payload
        snapshot = event_payload.get("snapshot")
        if not isinstance(snapshot, Mapping) or event_payload.get("snapshot_digest") != _digest(snapshot):
            raise GraphCheckpointMismatch("graph journal snapshot digest is missing or invalid")
        expected = {
            "run_id": self.run_id, "plan_digest": self.plan_digest,
            "intent_digest": self.intent_digest, "source_revision": self.source_revision,
        }
        for key, value in expected.items():
            if snapshot.get(key) != value or checkpoint.payload.get(key) != value:
                raise GraphCheckpointMismatch(f"graph checkpoint identity mismatch for {key}")
        if checkpoint.payload.get("snapshot_digest") != _digest(snapshot):
            raise GraphCheckpointMismatch("ledger snapshot digest differs from journal")
        if checkpoint.payload.get("contract") != "auren-graph-run-ledger-pointer-v1":
            raise GraphCheckpointMismatch("unsupported graph ledger pointer contract")
        if checkpoint.payload.get("run_status") != snapshot.get("run_status"):
            raise GraphCheckpointMismatch("ledger run status differs from journal snapshot")
        if checkpoint.payload.get("node_count") != len(snapshot.get("nodes", {})):
            raise GraphCheckpointMismatch("ledger node count differs from journal snapshot")
        if checkpoint.payload.get("next_safe_action") != snapshot.get("next_safe_action"):
            raise GraphCheckpointMismatch("ledger next action differs from journal snapshot")
        if checkpoint.head_sha != self.source_revision:
            raise GraphCheckpointMismatch("graph checkpoint source revision mismatch")
        if checkpoint.status == "verified" and snapshot.get("run_status") != "complete":
            raise GraphCheckpointMismatch("ledger reports verified while graph snapshot is incomplete")
        if checkpoint.status in {"blocked", "rolled_back"} and snapshot.get("run_status") not in {"blocked", "cancelled", "failed"}:
            raise GraphCheckpointMismatch("ledger reports blocked while graph snapshot is not blocked")
        return json.loads(_canonical(dict(snapshot)))

    @staticmethod
    def _priority_key(priority: Any) -> int:
        if isinstance(priority, int) and not isinstance(priority, bool):
            return -priority
        normalized = str(priority or "medium").strip().lower()
        named = {"critical": 0, "urgent": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}
        if normalized in named:
            return named[normalized]
        try:
            return -int(normalized)
        except ValueError:
            return named["medium"]

    def dispatchable_nodes(self) -> tuple[str, ...]:
        snapshot = self.load()
        if snapshot["run_status"] != "in_progress" or snapshot["budget_snapshot"].get("cancelled") is True:
            return ()
        nodes = snapshot["nodes"]
        ready = [
            node_id for node_id, node in nodes.items()
            if node["status"] == "waiting"
            and all(nodes[dependency]["status"] == "complete" for dependency in node["dependencies"])
        ]
        return tuple(sorted(ready, key=lambda node_id: (
            self._priority_key(getattr(self.plan.tasks[node_id], "priority", "medium")), node_id
        )))

    def record_node(
        self,
        *,
        event_id: str,
        node_id: str,
        status: str,
        budget_snapshot: Mapping[str, Any],
        next_safe_action: str,
        worker_id: str | None = None,
        workspace_id: str | None = None,
        attempts: int | None = None,
        evidence_refs: Sequence[str] = (),
        blockers: Sequence[str] = (),
    ) -> dict[str, Any]:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id is required for idempotent durable progress")
        if status not in _GRAPH_STATUSES:
            raise ValueError(f"invalid graph node status: {status}")
        if not isinstance(next_safe_action, str) or not next_safe_action.strip():
            raise ValueError("next_safe_action is required at every durable boundary")
        snapshot = self.load()
        if snapshot["run_status"] != "in_progress":
            raise GraphCheckpointMismatch(f"graph run is {snapshot['run_status']}; automatic dispatch is blocked")
        if node_id not in snapshot["nodes"]:
            raise KeyError(f"unknown graph node: {node_id}")
        node = snapshot["nodes"][node_id]
        if status not in _TRANSITIONS[node["status"]]:
            raise ValueError(f"invalid graph node transition: {node['status']} -> {status}")
        current_budget = _validate_budget_snapshot(budget_snapshot)
        _assert_budget_monotonic(snapshot["budget_snapshot"], current_budget)
        _assert_budget_monotonic(node["budget_snapshot"], current_budget)
        prior_attempts = node["attempts"]
        new_attempts = prior_attempts if attempts is None else attempts
        if type(new_attempts) is not int or new_attempts < prior_attempts:
            raise ValueError("node attempts cannot decrease or be non-integer")
        if node["status"] in {"failed", "blocked"} and status == "waiting" and new_attempts <= prior_attempts:
            raise ValueError("retrying a failed or blocked node requires a higher attempt count")
        if status in {"working", "adopting"}:
            if not isinstance(worker_id, str) or not worker_id.strip() or not isinstance(workspace_id, str) or not workspace_id.strip():
                raise ValueError("working nodes require worker_id and workspace_id")
            if any(snapshot["nodes"][dependency]["status"] != "complete" for dependency in node["dependencies"]):
                raise ValueError("node cannot start before all dependencies are complete")
        clean_blockers = sorted({str(item).strip() for item in blockers if str(item).strip()})
        if status == "blocked" and not clean_blockers:
            clean_blockers = [
                f"dependency:{dependency}" for dependency in node["dependencies"]
                if snapshot["nodes"][dependency]["status"] != "complete"
            ]
            if not clean_blockers:
                raise ValueError("blocked node must record an actionable blocker")
        if status != "blocked" and clean_blockers:
            raise ValueError("non-blocked node cannot retain blockers")
        refs = list(dict.fromkeys(str(item).strip() for item in evidence_refs if str(item).strip()))
        if status == "adopting" and not refs:
            raise ValueError("adopting nodes require verified proposal and receipt evidence")
        if status == "complete":
            if current_budget.get("remaining_seconds") is not None and current_budget["remaining_seconds"] <= 0:
                raise ValueError("expired deadline cannot produce a completed node")
            if any(snapshot["nodes"][dependency]["status"] != "complete" for dependency in node["dependencies"]):
                raise ValueError("node cannot complete before all dependencies are complete")
            if not refs:
                raise ValueError("node completion requires concrete evidence references")
        updated_node = {
            "status": status,
            "dependencies": node["dependencies"],
            "blockers": clean_blockers,
            "worker_id": worker_id.strip() if isinstance(worker_id, str) and worker_id.strip() else node["worker_id"],
            "workspace_id": workspace_id.strip() if isinstance(workspace_id, str) and workspace_id.strip() else node["workspace_id"],
            "attempts": new_attempts,
            "budget_snapshot": current_budget,
            "evidence_refs": refs if refs else node["evidence_refs"],
            "next_safe_action": next_safe_action.strip(),
        }
        nodes = dict(snapshot["nodes"])
        nodes[node_id] = updated_node
        all_complete = all(item["status"] == "complete" and item["evidence_refs"] for item in nodes.values())
        updated = {
            **snapshot,
            "nodes": nodes,
            "budget_snapshot": current_budget,
            "run_status": "complete" if all_complete else "in_progress",
            "next_safe_action": "verify_graph_receipt_and_stop" if all_complete else next_safe_action.strip(),
        }
        event = self.journal.append(
            event_id=f"graph:{self.run_id}:{event_id.strip()}",
            work_id=self.run_id, state="graph_checkpoint",
            payload={"snapshot": updated, "snapshot_digest": _digest(updated), "event_kind": "node_transition", "node_id": node_id},
        )
        self._publish_pointer(event, updated)
        return updated

    def block_run(self, *, event_id: str, reason: str, next_safe_action: str, budget_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("blocked run requires a reason")
        if not isinstance(next_safe_action, str) or not next_safe_action.strip():
            raise ValueError("blocked run requires a next safe action")
        snapshot = self.load()
        if snapshot["run_status"] != "in_progress":
            raise GraphCheckpointMismatch(f"cannot block run from state {snapshot['run_status']}")
        budget = _validate_budget_snapshot(budget_snapshot)
        _assert_budget_monotonic(snapshot["budget_snapshot"], budget)
        updated = {
            **snapshot, "run_status": "blocked", "block_reason": reason.strip()[:500],
            "budget_snapshot": budget, "next_safe_action": next_safe_action.strip(),
        }
        event = self.journal.append(
            event_id=f"graph:{self.run_id}:{event_id.strip()}", work_id=self.run_id,
            state="graph_checkpoint",
            payload={"snapshot": updated, "snapshot_digest": _digest(updated), "event_kind": "run_blocked"},
        )
        self._publish_pointer(event, updated)
        return updated

    def reconcile(self, *, reason: str, expected_snapshot_digest: str) -> dict[str, Any]:
        """Explicitly reconcile a journal-ahead split write; never auto-select a winner."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reconciliation reason is required")
        if not _SHA256.fullmatch(expected_snapshot_digest or ""):
            raise ValueError("expected_snapshot_digest must be a SHA-256 digest")
        events = self._events()
        if not events:
            raise GraphCheckpointMismatch("no graph journal event is available to reconcile")
        latest = events[-1]
        snapshot = latest.payload.get("snapshot")
        if not isinstance(snapshot, Mapping) or latest.payload.get("snapshot_digest") != _digest(snapshot):
            raise GraphCheckpointMismatch("latest graph journal event is invalid")
        if _digest(snapshot) != expected_snapshot_digest:
            raise GraphCheckpointMismatch("operator-supplied snapshot digest does not match latest journal event")
        for key, value in (
            ("run_id", self.run_id), ("plan_digest", self.plan_digest),
            ("intent_digest", self.intent_digest), ("source_revision", self.source_revision),
        ):
            if snapshot.get(key) != value:
                raise GraphCheckpointMismatch(f"cannot reconcile graph identity mismatch for {key}")
        self.ledger.start_run(self.run_id, self._immutable_intent)
        self._publish_pointer(latest, snapshot, reason=reason)
        return self.load()


    def cancel_run(
        self,
        *,
        event_id: str,
        reason: str,
        next_safe_action: str,
        budget_snapshot: Mapping[str, Any],
        confirmed_stopped_nodes: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Persist cancellation without claiming an unconfirmed worker has stopped."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancellation reason is required")
        if not isinstance(next_safe_action, str) or not next_safe_action.strip():
            raise ValueError("cancellation requires a next safe action")
        snapshot = self.load()
        if snapshot["run_status"] != "in_progress":
            raise GraphCheckpointMismatch(f"cannot cancel graph run from state {snapshot['run_status']}")
        budget = _validate_budget_snapshot(budget_snapshot)
        _assert_budget_monotonic(snapshot["budget_snapshot"], budget)
        if budget.get("cancelled") is not True:
            raise ValueError("canonical RuntimeExecutionGuard must confirm cancellation before run cancellation is persisted")
        confirmed = set(confirmed_stopped_nodes)
        unknown_ids = confirmed - set(snapshot["nodes"])
        if unknown_ids:
            raise ValueError("confirmed stop set contains unknown nodes: " + ", ".join(sorted(unknown_ids)))
        nodes = {}
        for node_id, old in snapshot["nodes"].items():
            node = dict(old)
            if node["status"] == "waiting":
                node["status"] = "cancelled"
                node["next_safe_action"] = next_safe_action.strip()
            elif node["status"] == "adopting":
                # A stopped adoption process may have applied some or all of its
                # patch. Process termination alone cannot establish target state.
                node["status"] = "unknown"
                node["next_safe_action"] = "reconcile_target_tree_and_recovery_ledger"
            elif node["status"] == "working":
                node["status"] = "cancelled" if node_id in confirmed else "unknown"
                node["next_safe_action"] = (
                    "inspect_partial_results_before_resume" if node_id in confirmed
                    else "reconcile_worker_termination_before_resume"
                )
            nodes[node_id] = node
        updated = {
            **snapshot, "nodes": nodes, "run_status": "cancelled",
            "cancel_reason": reason.strip()[:500], "budget_snapshot": budget,
            "next_safe_action": next_safe_action.strip(),
        }
        event = self.journal.append(
            event_id=f"graph:{self.run_id}:{event_id.strip()}", work_id=self.run_id,
            state="graph_checkpoint",
            payload={"snapshot": updated, "snapshot_digest": _digest(updated), "event_kind": "run_cancelled"},
        )
        self._publish_pointer(event, updated)
        return updated

