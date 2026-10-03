"""Bounded hypothesis and belief-update primitives for AUREN.

The engine records competing hypotheses and separates supporting evidence from
contradicting evidence. It never turns confidence into permission to act;
execution remains governed by the existing orchestration and safety layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any

from .persistent_memory import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    question: str
    statement: str
    source: str
    confidence: float = 0.5
    status: str = "open"
    created_at: str = ""

    def __post_init__(self) -> None:
        for name, value in (("hypothesis_id", self.hypothesis_id), ("question", self.question),
                            ("statement", self.statement), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.status not in {"open", "supported", "refuted", "inconclusive"}:
            raise ValueError("status must be open, supported, refuted, or inconclusive")
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc())
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))


@dataclass(frozen=True)
class BeliefEvidence:
    evidence_id: str
    hypothesis_id: str
    supports: bool
    detail: str
    source: str
    confidence: float = 1.0
    observed_at: str = ""

    def __post_init__(self) -> None:
        for name, value in (("evidence_id", self.evidence_id), ("hypothesis_id", self.hypothesis_id),
                            ("detail", self.detail), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.observed_at:
            object.__setattr__(self, "observed_at", _utc())
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))


class HypothesisEngine:
    """Persist competing hypotheses and compute bounded evidence-weighted belief."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_hypotheses: int = 20_000,
                 max_evidence: int = 100_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if min(max_hypotheses, max_evidence) < 1:
            raise ValueError("hypothesis bounds must be positive")
        self.memory = memory
        self.project = project
        self.max_hypotheses = max_hypotheses
        self.max_evidence = max_evidence
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.memory.path, timeout=10)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS hypotheses(
                    project TEXT NOT NULL,
                    hypothesis_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project, hypothesis_id));
                CREATE TABLE IF NOT EXISTS hypothesis_evidence(
                    project TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    hypothesis_id TEXT NOT NULL,
                    supports INTEGER NOT NULL,
                    detail TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(project, evidence_id));
                CREATE INDEX IF NOT EXISTS idx_hypothesis_evidence ON hypothesis_evidence(project, hypothesis_id, supports, observed_at, evidence_id);"""
            )

    def propose(self, hypothesis: Hypothesis) -> Hypothesis:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT question,statement,source,confidence,status,created_at FROM hypotheses WHERE project=? AND hypothesis_id=?",
                (self.project, hypothesis.hypothesis_id),
            ).fetchone()
            if row:
                if row != (hypothesis.question, hypothesis.statement, hypothesis.source, hypothesis.confidence, hypothesis.status, hypothesis.created_at):
                    raise ValueError("hypothesis_id already exists with different content")
                return hypothesis
            count = db.execute("SELECT COUNT(*) FROM hypotheses WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_hypotheses:
                raise ValueError("hypothesis budget exceeded")
            db.execute("INSERT INTO hypotheses VALUES(?,?,?,?,?,?,?,?)", (self.project, hypothesis.hypothesis_id,
                       hypothesis.question, hypothesis.statement, hypothesis.source, hypothesis.confidence,
                       hypothesis.status, hypothesis.created_at))
        return hypothesis

    def add_evidence(self, evidence: BeliefEvidence) -> BeliefEvidence:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM hypotheses WHERE project=? AND hypothesis_id=?", (self.project, evidence.hypothesis_id)).fetchone():
                raise KeyError("hypothesis does not exist")
            existing = db.execute("SELECT hypothesis_id,supports,detail,source,confidence,observed_at FROM hypothesis_evidence WHERE project=? AND evidence_id=?",
                                  (self.project, evidence.evidence_id)).fetchone()
            expected = (evidence.hypothesis_id, int(evidence.supports), evidence.detail, evidence.source, evidence.confidence, evidence.observed_at)
            if existing:
                if existing != expected:
                    raise ValueError("evidence_id already exists with different content")
                return evidence
            count = db.execute("SELECT COUNT(*) FROM hypothesis_evidence WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_evidence:
                raise ValueError("hypothesis evidence budget exceeded")
            db.execute("INSERT INTO hypothesis_evidence VALUES(?,?,?,?,?,?,?,?)", (self.project, evidence.evidence_id,
                       evidence.hypothesis_id, int(evidence.supports), evidence.detail, evidence.source,
                       evidence.confidence, evidence.observed_at))
        return evidence

    def assess(self, hypothesis_id: str) -> Hypothesis:
        with self._connect() as db:
            row = db.execute("SELECT question,statement,source,confidence,status,created_at FROM hypotheses WHERE project=? AND hypothesis_id=?",
                             (self.project, hypothesis_id)).fetchone()
            if not row:
                raise KeyError("hypothesis does not exist")
            evidence = db.execute("SELECT supports,confidence FROM hypothesis_evidence WHERE project=? AND hypothesis_id=? ORDER BY observed_at,evidence_id",
                                  (self.project, hypothesis_id)).fetchall()
        support = sum(float(confidence) for supports, confidence in evidence if supports)
        contradiction = sum(float(confidence) for supports, confidence in evidence if not supports)
        total = support + contradiction
        if total == 0:
            confidence = float(row[3])
            status = row[4]
        else:
            confidence = support / total
            status = "supported" if confidence >= 0.8 else "refuted" if confidence <= 0.2 else "inconclusive"
        updated = Hypothesis(hypothesis_id, row[0], row[1], row[2], round(confidence, 6), status, row[5])
        with self._connect() as db:
            db.execute("UPDATE hypotheses SET confidence=?,status=? WHERE project=? AND hypothesis_id=?",
                       (updated.confidence, updated.status, self.project, hypothesis_id))
        return updated

    def evidence(self, hypothesis_id: str) -> tuple[BeliefEvidence, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT evidence_id,hypothesis_id,supports,detail,source,confidence,observed_at FROM hypothesis_evidence WHERE project=? AND hypothesis_id=? ORDER BY observed_at,evidence_id",
                              (self.project, hypothesis_id)).fetchall()
        return tuple(BeliefEvidence(row[0], row[1], bool(row[2]), row[3], row[4], float(row[5]), row[6]) for row in rows)

    def digest(self, hypothesis_id: str) -> str:
        hypothesis = self.assess(hypothesis_id)
        payload: dict[str, Any] = {"hypothesis": hypothesis.__dict__, "evidence": [item.__dict__ for item in self.evidence(hypothesis_id)]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()[:16]


__all__ = ["BeliefEvidence", "Hypothesis", "HypothesisEngine"]
