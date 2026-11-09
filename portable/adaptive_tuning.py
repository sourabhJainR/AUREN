"""Empirical policy tuning over long-running task experience.

The tuner is intentionally bounded: it can change future strategy selection,
confidence calibration and iteration targets, but it cannot change execution
or safety authority. All policy versions are immutable and evidence-linked.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from .persistent_memory import PersistentMemory


MIN_OBSERVATIONS = 6
MIN_INDEPENDENT_TASKS = 4
MAX_CONFIDENCE_ADJUSTMENT = 0.10
MIN_ITERATION_TARGET = 1.0
MAX_ITERATION_TARGET = 32.0


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExperienceRecord:
    record_id: str
    project: str
    task_id: str
    capability: str
    strategy: str
    quality: float
    iterations: int
    confidence: float
    verified: bool
    evidence: tuple[str, ...]
    evaluation_class: str
    realized_success: bool
    created_at: str
    digest: str


@dataclass(frozen=True)
class AdaptivePolicy:
    project: str
    scope: str
    version: str
    parent_version: str | None
    strategy: str
    confidence_adjustment: float
    iteration_target: float
    created_at: str
    status: str
    evidence_digest: str


@dataclass(frozen=True)
class TuningDecision:
    scope: str
    action: str
    strategy: str
    confidence_adjustment: float
    iteration_target: float
    previous_iteration_target: float
    observations: int
    independent_tasks: int
    reason: str
    evidence_digest: str
    policy_version: str


@dataclass(frozen=True)
class MaintenanceReceipt:
    cycle_id: str
    project: str
    started_at: str
    finished_at: str
    jobs_processed: int
    strategy_action: str
    policy_version: str
    errors: tuple[str, ...]
    digest: str


class AdaptiveTuner:
    """Persist experience and graduate empirically validated future policy."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not project.strip():
            raise ValueError("project is required")
        self.memory = memory
        self.project = project.strip()
        with self.memory._lock, self.memory._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS adaptive_experience_history(
                project TEXT NOT NULL, record_id TEXT NOT NULL, task_id TEXT NOT NULL,
                capability TEXT NOT NULL, strategy TEXT NOT NULL, quality REAL NOT NULL,
                iterations INTEGER NOT NULL, confidence REAL NOT NULL, verified INTEGER NOT NULL,
                evidence TEXT NOT NULL, evaluation_class TEXT NOT NULL,
                realized_success INTEGER NOT NULL, created_at TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(project, record_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_history_scope ON adaptive_experience_history(project, capability, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_history_strategy ON adaptive_experience_history(project, capability, strategy, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS adaptive_policies(
                project TEXT NOT NULL, scope TEXT NOT NULL, version TEXT NOT NULL,
                parent_version TEXT, strategy TEXT NOT NULL, confidence_adjustment REAL NOT NULL,
                iteration_target REAL NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
                evidence_digest TEXT NOT NULL, PRIMARY KEY(project, scope, version))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_policy_current ON adaptive_policies(project, scope, status, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS adaptive_maintenance_receipts(
                project TEXT NOT NULL, cycle_id TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL, jobs_processed INTEGER NOT NULL, strategy_action TEXT NOT NULL,
                policy_version TEXT NOT NULL, errors TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(project, cycle_id))""")
            row = db.execute("SELECT 1 FROM adaptive_policies WHERE project=? AND scope=? LIMIT 1", (self.project, "global")).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO adaptive_policies VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (self.project, "global", "v1", None, "default", 0.0, 2.0, _utc(), "active", _digest({"scope": "global", "version": "v1"})),
                )

    def record_experience(
        self,
        *,
        task_id: str,
        capability: str,
        strategy: str,
        quality: float,
        iterations: int,
        confidence: float,
        verified: bool,
        evidence: Iterable[str] = (),
        evaluation_class: str = "experience",
        realized_success: bool | None = None,
    ) -> ExperienceRecord:
        if not task_id.strip() or not capability.strip() or not strategy.strip():
            raise ValueError("task_id, capability and strategy are required")
        if not 0 <= quality <= 1 or not 0 <= confidence <= 1:
            raise ValueError("quality and confidence must be between 0 and 1")
        if iterations < 1:
            raise ValueError("iterations must be positive")
        clean_evidence = tuple(sorted({item.strip() for item in evidence if isinstance(item, str) and item.strip()}))
        record_id = uuid4().hex
        success = bool(realized_success) if realized_success is not None else bool(verified and quality >= 0.9)
        created_at = _utc()
        payload = {
            "record_id": record_id,
            "task_id": task_id.strip(),
            "capability": capability.strip(),
            "strategy": strategy.strip(),
            "quality": round(float(quality), 6),
            "iterations": int(iterations),
            "confidence": round(float(confidence), 6),
            "verified": bool(verified),
            "evidence": clean_evidence,
            "evaluation_class": evaluation_class.strip() or "experience",
            "realized_success": success,
            "created_at": created_at,
        }
        record_digest = _digest(payload)
        with self.memory._lock, self.memory._connection() as db:
            db.execute(
                "INSERT INTO adaptive_experience_history VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.project, record_id, payload["task_id"], payload["capability"], payload["strategy"],
                 payload["quality"], payload["iterations"], payload["confidence"], int(payload["verified"]),
                 json.dumps(clean_evidence), payload["evaluation_class"], int(success), created_at, record_digest),
            )
        return ExperienceRecord(record_id, self.project, payload["task_id"], payload["capability"], payload["strategy"],
                                float(payload["quality"]), int(payload["iterations"]), float(payload["confidence"]),
                                bool(payload["verified"]), clean_evidence, payload["evaluation_class"], success,
                                created_at, record_digest)

    def history(self, *, scope: str | None = None, limit: int = 200) -> tuple[ExperienceRecord, ...]:
        if limit < 1:
            return ()
        scope = scope or "global"
        with self.memory._lock, self.memory._connection() as db:
            rows = db.execute(
                "SELECT record_id,task_id,capability,strategy,quality,iterations,confidence,verified,evidence,evaluation_class,realized_success,created_at,digest "
                "FROM adaptive_experience_history WHERE project=? AND (?='global' OR capability=?) ORDER BY created_at,record_id LIMIT ?",
                (self.project, scope, scope, int(limit)),
            ).fetchall()
        return tuple(
            ExperienceRecord(row[0], self.project, row[1], row[2], row[3], float(row[4]), int(row[5]), float(row[6]),
                             bool(row[7]), tuple(json.loads(row[8])), row[9], bool(row[10]), row[11], row[12])
            for row in rows
        )

    def current_policy(self, scope: str = "global") -> AdaptivePolicy:
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute(
                "SELECT version,parent_version,strategy,confidence_adjustment,iteration_target,created_at,status,evidence_digest "
                "FROM adaptive_policies WHERE project=? AND scope=? AND status='active' ORDER BY created_at DESC,version DESC LIMIT 1",
                (self.project, scope),
            ).fetchone()
            if row is None and scope != "global":
                return self.current_policy("global")
            if row is None:
                raise RuntimeError("adaptive global policy missing")
        return AdaptivePolicy(self.project, scope, row[0], row[1], row[2], float(row[3]), float(row[4]), row[5], row[6], row[7])

    def evaluate(self, scope: str, *, candidate_strategy: str | None = None) -> TuningDecision:
        active = self.current_policy(scope)
        records = self.history(scope=scope, limit=1000)
        independent = len({record.task_id for record in records})
        if len(records) < MIN_OBSERVATIONS or independent < MIN_INDEPENDENT_TASKS:
            return TuningDecision(scope, "hold", active.strategy, active.confidence_adjustment, active.iteration_target,
                                   active.iteration_target, len(records), independent, "insufficient independent history",
                                   self._history_digest(records), active.version)

        confidence_adjustment = self._confidence_adjustment(records)
        strategy = active.strategy
        strategy_reason = "current strategy retained"
        action = "hold"
        if candidate_strategy and candidate_strategy != active.strategy:
            baseline = [r for r in records if r.strategy == active.strategy and r.evaluation_class in {"experience", "adaptation"}]
            candidate = [r for r in records if r.strategy == candidate_strategy and r.evaluation_class in {"experience", "adaptation"}]
            holdout = [r for r in records if r.strategy == candidate_strategy and r.evaluation_class in {"holdout", "transfer"}]
            if len({r.task_id for r in candidate}) >= MIN_INDEPENDENT_TASKS and len(holdout) >= 2 and baseline and candidate:
                base_quality = sum(r.quality for r in baseline) / len(baseline)
                cand_quality = sum(r.quality for r in candidate) / len(candidate)
                base_iterations = sum(r.iterations for r in baseline) / len(baseline)
                cand_iterations = sum(r.iterations for r in candidate) / len(candidate)
                holdout_quality = sum(r.quality for r in holdout) / len(holdout)
                if holdout_quality >= base_quality - 0.01 and (cand_quality >= base_quality + 0.02 or cand_iterations <= base_iterations - 0.75):
                    strategy = candidate_strategy
                    strategy_reason = "candidate improves quality or iterations and passes holdout"
                    action = "promote"

        iteration_target = active.iteration_target
        adaptation = [r for r in records if r.evaluation_class in {"adaptation", "experience"} and r.verified]
        holdout = [r for r in records if r.evaluation_class in {"holdout", "transfer"} and r.verified]
        if adaptation and holdout:
            adapted_iterations = sum(r.iterations for r in adaptation) / len(adaptation)
            adapted_quality = sum(r.quality for r in adaptation) / len(adaptation)
            holdout_quality = sum(r.quality for r in holdout) / len(holdout)
            if adapted_iterations <= active.iteration_target - 0.75 and adapted_quality >= 0.9 and holdout_quality >= adapted_quality - 0.05:
                iteration_target = _clamp(active.iteration_target - 0.5, MIN_ITERATION_TARGET, MAX_ITERATION_TARGET)
                if action == "hold":
                    action = "adjust"
            elif holdout_quality < adapted_quality - 0.10:
                iteration_target = active.iteration_target

        changed = action != "hold" or abs(confidence_adjustment - active.confidence_adjustment) >= 0.02 or abs(iteration_target - active.iteration_target) >= 0.25
        evidence_digest = self._history_digest(records)
        if not changed:
            return TuningDecision(scope, "hold", active.strategy, active.confidence_adjustment, active.iteration_target,
                                  active.iteration_target, len(records), independent, "history does not support a bounded change",
                                  evidence_digest, active.version)
        return self._promote(scope, active, strategy, confidence_adjustment, iteration_target, len(records), independent,
                             strategy_reason, evidence_digest, action)

    def record_maintenance_receipt(
        self,
        *,
        started_at: str,
        jobs_processed: int,
        strategy_action: str,
        policy_version: str,
        errors: Iterable[str] = (),
        cycle_id: str | None = None,
    ) -> MaintenanceReceipt:
        finished_at = _utc()
        cycle_id = cycle_id or uuid4().hex
        clean_errors = tuple(sorted({str(item) for item in errors if str(item)}))
        payload = {
            "cycle_id": cycle_id,
            "project": self.project,
            "started_at": started_at,
            "finished_at": finished_at,
            "jobs_processed": int(jobs_processed),
            "strategy_action": strategy_action,
            "policy_version": policy_version,
            "errors": clean_errors,
        }
        digest = _digest(payload)
        with self.memory._lock, self.memory._connection() as db:
            db.execute(
                "INSERT INTO adaptive_maintenance_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                (self.project, cycle_id, started_at, finished_at, int(jobs_processed), strategy_action, policy_version,
                 json.dumps(clean_errors), digest),
            )
        return MaintenanceReceipt(cycle_id, self.project, started_at, finished_at, int(jobs_processed), strategy_action,
                                  policy_version, clean_errors, digest)

    def recent_receipts(self, limit: int = 20) -> tuple[MaintenanceReceipt, ...]:
        with self.memory._lock, self.memory._connection() as db:
            rows = db.execute(
                "SELECT cycle_id,started_at,finished_at,jobs_processed,strategy_action,policy_version,errors,digest "
                "FROM adaptive_maintenance_receipts WHERE project=? ORDER BY finished_at DESC,cycle_id DESC LIMIT ?",
                (self.project, int(limit)),
            ).fetchall()
        return tuple(MaintenanceReceipt(row[0], self.project, row[1], row[2], int(row[3]), row[4], row[5], tuple(json.loads(row[6])), row[7]) for row in rows)

    def _promote(self, scope: str, active: AdaptivePolicy, strategy: str, confidence_adjustment: float,
                 iteration_target: float, observations: int, independent: int, reason: str,
                 evidence_digest: str, action: str) -> TuningDecision:
        version = f"v{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        with self.memory._lock, self.memory._connection() as db:
            db.execute("UPDATE adaptive_policies SET status='retired' WHERE project=? AND scope=? AND status='active'", (self.project, scope))
            db.execute(
                "INSERT INTO adaptive_policies VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project, scope, version, active.version, strategy, _clamp(confidence_adjustment, -0.25, 0.25),
                 _clamp(iteration_target, MIN_ITERATION_TARGET, MAX_ITERATION_TARGET), _utc(), "active", evidence_digest),
            )
        return TuningDecision(scope, action, strategy, _clamp(confidence_adjustment, -0.25, 0.25),
                              _clamp(iteration_target, MIN_ITERATION_TARGET, MAX_ITERATION_TARGET), active.iteration_target,
                              observations, independent, reason, evidence_digest, version)

    @staticmethod
    def _confidence_adjustment(records: Iterable[ExperienceRecord]) -> float:
        calibrated = [record for record in records if record.verified]
        if len(calibrated) < MIN_OBSERVATIONS:
            return 0.0
        error = sum((1.0 if record.realized_success else 0.0) - record.confidence for record in calibrated) / len(calibrated)
        return _clamp(error * 0.25, -MAX_CONFIDENCE_ADJUSTMENT, MAX_CONFIDENCE_ADJUSTMENT)

    @staticmethod
    def _history_digest(records: Iterable[ExperienceRecord]) -> str:
        return _digest({"records": [record.digest for record in records]})


__all__ = ["AdaptivePolicy", "AdaptiveTuner", "ExperienceRecord", "MaintenanceReceipt", "TuningDecision"]
