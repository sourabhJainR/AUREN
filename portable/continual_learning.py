"""Replayable benchmark history and regression gates for continual learning."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .persistent_memory import PersistentMemory


@dataclass(frozen=True)
class BenchmarkObservation:
    suite: str
    capability: str
    version: str
    score: float
    sample_count: int
    evidence_ids: tuple[str, ...]
    verified: bool = False
    approved: bool = False

    def __post_init__(self) -> None:
        for name, value in (("suite", self.suite), ("capability", self.capability), ("version", self.version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between 0 and 1")
        if self.sample_count < 1:
            raise ValueError("sample_count must be positive")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_ids):
            raise ValueError("evidence_ids must contain non-empty strings")
        if self.verified and not self.evidence_ids:
            raise ValueError("verified observations require evidence_ids")


@dataclass(frozen=True)
class RegressionResult:
    suite: str
    capability: str
    baseline_version: str | None
    current_version: str
    baseline_score: float | None
    current_score: float
    delta: float | None
    tolerance: float
    accepted: bool
    regressed: bool


class ContinualLearningGuard:
    """Persist benchmark observations and block regressions beyond a tolerance."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        self.memory = memory
        self.project = project.strip()
        with self.memory._lock, self.memory._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS continual_benchmarks(
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                suite TEXT NOT NULL,
                capability TEXT NOT NULL,
                version TEXT NOT NULL,
                score REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                evidence_ids TEXT NOT NULL,
                verified INTEGER NOT NULL,
                approved INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(project, suite, capability, version)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_continual_history
                ON continual_benchmarks(project, suite, capability, recorded_at)""")

    def record(self, observation: BenchmarkObservation) -> bool:
        if not observation.verified or not observation.evidence_ids:
            return False
        if self.memory.require_approval and not observation.approved:
            return False
        evidence = tuple(sorted(set(observation.evidence_ids)))
        payload = f"{self.project}|{observation.suite}|{observation.capability}|{observation.version}|{observation.score}|{observation.sample_count}|{json.dumps(evidence)}"
        row_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        recorded_at = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connection() as db:
            existing = db.execute("SELECT score,sample_count,evidence_ids,verified,approved FROM continual_benchmarks WHERE project=? AND suite=? AND capability=? AND version=?",
                                  (self.project, observation.suite, observation.capability, observation.version)).fetchone()
            incoming = (observation.score, observation.sample_count, json.dumps(evidence), int(observation.verified), int(observation.approved))
            if existing:
                if existing != incoming:
                    raise ValueError("benchmark version already exists with different content")
                return True
            db.execute("INSERT INTO continual_benchmarks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (row_id, self.project, observation.suite, observation.capability, observation.version,
                         observation.score, observation.sample_count, json.dumps(evidence), int(observation.verified),
                         int(observation.approved), recorded_at))
        return True

    def compare(self, observation: BenchmarkObservation, *, tolerance: float = 0.02) -> RegressionResult:
        if not 0 <= tolerance <= 1:
            raise ValueError("tolerance must be between 0 and 1")
        if not observation.verified or not observation.evidence_ids or (self.memory.require_approval and not observation.approved):
            return RegressionResult(observation.suite, observation.capability, None, observation.version,
                                    None, observation.score, None, tolerance, False, True)
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute("""SELECT version,score FROM continual_benchmarks
                WHERE project=? AND suite=? AND capability=?
                ORDER BY recorded_at DESC, version DESC LIMIT 1""",
                             (self.project, observation.suite, observation.capability)).fetchone()
        if row is None:
            return RegressionResult(observation.suite, observation.capability, None, observation.version,
                                    None, observation.score, None, tolerance, True, False)
        baseline_version, baseline_score = str(row[0]), float(row[1])
        delta = observation.score - baseline_score
        regressed = delta < -tolerance
        return RegressionResult(observation.suite, observation.capability, baseline_version, observation.version,
                                baseline_score, observation.score, delta, tolerance, not regressed, regressed)

    def history(self, suite: str, capability: str, *, limit: int = 50) -> tuple[BenchmarkObservation, ...]:
        if limit < 1:
            return ()
        with self.memory._lock, self.memory._connection() as db:
            rows = db.execute("""SELECT suite,capability,version,score,sample_count,evidence_ids,verified,approved
                FROM continual_benchmarks WHERE project=? AND suite=? AND capability=?
                ORDER BY recorded_at,version LIMIT ?""", (self.project, suite, capability, limit)).fetchall()
        return tuple(BenchmarkObservation(r[0], r[1], r[2], float(r[3]), int(r[4]), tuple(json.loads(r[5])), bool(r[6]), bool(r[7])) for r in rows)


__all__ = ["BenchmarkObservation", "ContinualLearningGuard", "RegressionResult"]
