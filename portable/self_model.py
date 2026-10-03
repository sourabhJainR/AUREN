"""Empirical self-model for AUREN capability calibration.

Capability estimates can be segmented by context and difficulty. The model
records outcomes only; it never grants permissions or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from .persistent_memory import PersistentMemory


@dataclass(frozen=True)
class CapabilityProfile:
    capability: str
    successes: int
    failures: int
    confidence: float
    observations: int
    context: str | None = None
    difficulty: int | None = None


class SelfModel:
    """Persist bounded empirical capability outcomes and calibrated profiles."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_outcomes: int = 100_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if max_outcomes < 1:
            raise ValueError("max_outcomes must be positive")
        self.memory = memory
        self.project = project.strip()
        self.max_outcomes = max_outcomes
        with sqlite3.connect(memory.path, timeout=10) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS self_model_outcomes(
                project TEXT NOT NULL, outcome_id TEXT NOT NULL, capability TEXT NOT NULL,
                success INTEGER NOT NULL, context TEXT, difficulty INTEGER,
                PRIMARY KEY(project, outcome_id))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(self_model_outcomes)").fetchall()}
            if "context" not in columns:
                db.execute("ALTER TABLE self_model_outcomes ADD COLUMN context TEXT")
            if "difficulty" not in columns:
                db.execute("ALTER TABLE self_model_outcomes ADD COLUMN difficulty INTEGER")
            db.execute("CREATE INDEX IF NOT EXISTS idx_self_model_capability ON self_model_outcomes(project, capability, context, difficulty, success, outcome_id)")

    @staticmethod
    def _validate_context(context: str | None) -> str | None:
        if context is not None and (not isinstance(context, str) or not context.strip()):
            raise ValueError("context must be a non-empty string when supplied")
        return context.strip() if context else None

    def record(self, outcome_id: str, capability: str, *, success: bool, context: str | None = None,
               difficulty: int | None = None) -> None:
        if not isinstance(outcome_id, str) or not outcome_id.strip() or not isinstance(capability, str) or not capability.strip():
            raise ValueError("outcome_id and capability are required")
        context = self._validate_context(context)
        if difficulty is not None and (not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 0 <= difficulty <= 10):
            raise ValueError("difficulty must be an integer between 0 and 10")
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT capability,success,context,difficulty FROM self_model_outcomes WHERE project=? AND outcome_id=?", (self.project, outcome_id)).fetchone()
            expected = (capability, int(success), context, difficulty)
            if existing:
                if existing != expected:
                    raise ValueError("outcome_id already exists with different content")
                return
            count = db.execute("SELECT COUNT(*) FROM self_model_outcomes WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_outcomes:
                raise ValueError("self-model outcome budget exceeded")
            db.execute("INSERT INTO self_model_outcomes VALUES(?,?,?,?,?,?)", (self.project, outcome_id, capability.strip(), int(success), context, difficulty))

    def profile(self, capability: str, *, context: str | None = None, difficulty: int | None = None) -> CapabilityProfile:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability is required")
        context = self._validate_context(context)
        if difficulty is not None and (not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 0 <= difficulty <= 10):
            raise ValueError("difficulty must be an integer between 0 and 10")
        clauses = ["project=?", "capability=?"]
        params: list[object] = [self.project, capability.strip()]
        if context is not None:
            clauses.append("context=?")
            params.append(context)
        if difficulty is not None:
            clauses.append("difficulty=?")
            params.append(difficulty)
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            row = db.execute(
                "SELECT SUM(success),SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),COUNT(*) FROM self_model_outcomes WHERE " + " AND ".join(clauses),
                tuple(params),
            ).fetchone()
        successes = int(row[0] or 0)
        failures = int(row[1] or 0)
        observations = int(row[2] or 0)
        confidence = successes / observations if observations else 0.0
        return CapabilityProfile(capability.strip(), successes, failures, round(confidence, 6), observations, context, difficulty)

    def should_escalate(self, capability: str, *, min_observations: int = 5, min_confidence: float = 0.8,
                        context: str | None = None, difficulty: int | None = None) -> bool:
        if min_observations < 1 or not 0 <= min_confidence <= 1:
            raise ValueError("escalation thresholds are invalid")
        profile = self.profile(capability, context=context, difficulty=difficulty)
        return profile.observations < min_observations or profile.confidence < min_confidence


__all__ = ["CapabilityProfile", "SelfModel"]
