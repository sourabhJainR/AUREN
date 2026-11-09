"""Belief-aware planning context and durable post-action learning signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable, Mapping
from uuid import uuid4

from .hypothesis_engine import BeliefEvidence, HypothesisEngine
from .self_model import SelfModel
from .world_model import PredictionError


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class BeliefContext:
    belief_id: str
    statement: str
    confidence: float
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.belief_id, str) or not self.belief_id.strip():
            raise ValueError("belief_id must be non-empty")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValueError("statement must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        evidence = tuple(sorted(set(item.strip() for item in self.evidence_ids if isinstance(item, str) and item.strip())))
        object.__setattr__(self, "evidence_ids", evidence)


@dataclass(frozen=True)
class LearningSignal:
    signal_id: str
    task_id: str
    intent: str
    status: str
    capability: str | None
    evidence: tuple[str, ...]
    digest: str
    created_at: str
    persistence_errors: tuple[str, ...] = ()
    prediction_id: str | None = None
    prediction_correct: bool | None = None


class CognitiveLearningLoop:
    """Project-scoped belief context and post-action learning adapter."""

    def __init__(self, memory, project: str, *, self_model: SelfModel | None = None, max_signals: int = 100_000) -> None:
        if not project or not project.strip():
            raise ValueError("project is required")
        if max_signals < 1:
            raise ValueError("max_signals must be positive")
        self.memory = memory
        self.project = project.strip()
        self.self_model = self_model or SelfModel(memory, self.project)
        self.hypotheses = HypothesisEngine(memory, self.project)
        self.max_signals = max_signals
        with self.memory._lock, self.memory._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cognitive_learning_signals(
                project TEXT NOT NULL, signal_id TEXT NOT NULL, task_id TEXT NOT NULL,
                intent TEXT NOT NULL, status TEXT NOT NULL, capability TEXT,
                evidence TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project, signal_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cognitive_learning_task ON cognitive_learning_signals(project, task_id, created_at)")

    @staticmethod
    def select_beliefs(beliefs: Iterable[BeliefContext], *, limit: int = 8) -> tuple[BeliefContext, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        normalized = list(beliefs)
        if any(not isinstance(item, BeliefContext) for item in normalized):
            raise TypeError("beliefs must contain BeliefContext values")
        return tuple(sorted(normalized, key=lambda item: (item.confidence, item.belief_id))[:limit])

    def beliefs(self, *, limit: int = 8) -> tuple[BeliefContext, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.memory._lock, self.memory._connection() as db:
            rows = db.execute(
                """SELECT h.hypothesis_id,h.statement,h.confidence,GROUP_CONCAT(e.evidence_id)
                   FROM hypotheses h
                   LEFT JOIN hypothesis_evidence e
                     ON e.project=h.project AND e.hypothesis_id=h.hypothesis_id
                  WHERE h.project=? AND h.status IN ('open','inconclusive')
                  GROUP BY h.hypothesis_id,h.statement,h.confidence
                  ORDER BY h.confidence ASC,h.hypothesis_id ASC LIMIT ?""",
                (self.project, limit),
            ).fetchall()
        return tuple(
            BeliefContext(row[0], row[1], float(row[2]), tuple(sorted(filter(None, (row[3] or "").split(",")))))
            for row in rows
        )

    def record(
        self,
        *,
        task_id: str,
        intent: str,
        status: str,
        capability: str | None = None,
        evidence: Iterable[str] = (),
        context: str | None = None,
        belief_evidence: Iterable[BeliefEvidence] = (),
        prediction_error: PredictionError | None = None,
    ) -> LearningSignal:
        if not task_id.strip() or not intent.strip() or not status.strip():
            raise ValueError("task_id, intent and status are required")
        clean_evidence = tuple(sorted(set(item.strip() for item in evidence if isinstance(item, str) and item.strip())))
        signal_id = uuid4().hex
        payload = {
            "task_id": task_id,
            "intent": intent,
            "status": status,
            "capability": capability,
            "evidence": clean_evidence,
            "context": context,
            "prediction_id": prediction_error.prediction_id if prediction_error else None,
            "prediction_correct": prediction_error.absolute_match if prediction_error else None,
        }
        digest = _digest(payload)
        persistence_errors: list[str] = []
        try:
            with self.memory._lock, self.memory._connection() as db:
                count = db.execute("SELECT COUNT(*) FROM cognitive_learning_signals WHERE project=?", (self.project,)).fetchone()[0]
                if count >= self.max_signals:
                    raise ValueError("cognitive learning signal budget exceeded")
                db.execute(
                    "INSERT INTO cognitive_learning_signals VALUES(?,?,?,?,?,?,?,?,?)",
                    (self.project, signal_id, task_id, intent, status, capability, json.dumps(clean_evidence), digest, _utc()),
                )
            for item in belief_evidence:
                self.hypotheses.add_evidence(item)
                self.hypotheses.assess(item.hypothesis_id)
            if capability:
                self.self_model.record(signal_id, capability, success=status in {"accepted", "completed", "success"}, context=context)
        except Exception as exc:
            persistence_errors.append(f"{type(exc).__name__}: {exc}")
        return LearningSignal(
            signal_id, task_id, intent, status, capability, clean_evidence, digest, _utc(),
            tuple(persistence_errors), prediction_error.prediction_id if prediction_error else None,
            prediction_error.absolute_match if prediction_error else None,
        )


__all__ = ["BeliefContext", "CognitiveLearningLoop", "LearningSignal"]
