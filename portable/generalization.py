"""Deterministic, evidence-gated structural generalization for AUREN."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .persistent_memory import PersistentMemory


def _clean(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


def _features(values: Iterable[str]) -> frozenset[str]:
    cleaned = {_clean(value, "feature") for value in values}
    return frozenset(sorted(cleaned))


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass(frozen=True)
class Abstraction:
    id: str
    name: str
    principle: str
    structure: frozenset[str]
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    negative_conditions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _clean(self.id, "id")
        _clean(self.name, "name")
        _clean(self.principle, "principle")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "structure", _features(self.structure))
        object.__setattr__(self, "evidence_ids", tuple(sorted({_clean(item, "evidence_id") for item in self.evidence_ids})))
        object.__setattr__(self, "negative_conditions", _features(self.negative_conditions))


@dataclass(frozen=True)
class AnalogyCandidate:
    abstraction_id: str
    name: str
    principle: str
    similarity: float
    evidence_ids: tuple[str, ...]
    confidence: float
    negative_conditions: frozenset[str]


class GeneralizationEngine:
    """Persist abstractions and retrieve deterministic structural analogies."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        self.memory = memory
        self.project = _clean(project, "project")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS generalization_abstractions(
                project TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                principle TEXT NOT NULL, structure TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                confidence REAL NOT NULL, negative_conditions TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS generalization_validations(
                project TEXT NOT NULL, abstraction_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project, abstraction_id, evidence_ids))""")

    def record(self, abstraction: Abstraction) -> None:
        if not isinstance(abstraction, Abstraction):
            raise ValueError("abstraction must be an Abstraction")
        if not abstraction.evidence_ids:
            raise ValueError("abstraction requires source evidence")
        if not abstraction.structure:
            raise ValueError("abstraction requires structural features")
        with self.memory._lock, self.memory._connect() as db:
            existing = db.execute(
                "SELECT name,principle,structure,evidence_ids,confidence,negative_conditions FROM generalization_abstractions WHERE project=? AND id=?",
                (self.project, abstraction.id),
            ).fetchone()
            incoming = (
                abstraction.name,
                abstraction.principle,
                json.dumps(sorted(abstraction.structure)),
                json.dumps(abstraction.evidence_ids),
                abstraction.confidence,
                json.dumps(sorted(abstraction.negative_conditions)),
            )
            if existing:
                if existing != incoming:
                    raise ValueError(f"abstraction id already exists with different content: {abstraction.id}")
                return
            db.execute(
                "INSERT INTO generalization_abstractions VALUES(?,?,?,?,?,?,?,?,?)",
                (self.project, abstraction.id, abstraction.name, abstraction.principle,
                 incoming[2], incoming[3], incoming[4], incoming[5], datetime.now(timezone.utc).isoformat()),
            )

    def find_analogies(self, structure: Iterable[str], *, target_conditions: Iterable[str] = (),
                       min_similarity: float = 0.0, limit: int = 10) -> list[AnalogyCandidate]:
        structure_set = _features(structure)
        target_set = _features(target_conditions)
        if not structure_set:
            return []
        if not 0 <= min_similarity <= 1:
            raise ValueError("min_similarity must be between 0 and 1")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                "SELECT id,name,principle,structure,evidence_ids,confidence,negative_conditions FROM generalization_abstractions WHERE project=?",
                (self.project,),
            ).fetchall()
        candidates: list[AnalogyCandidate] = []
        for row in rows:
            negative = _features(json.loads(row[6]))
            if negative & target_set:
                continue
            similarity = _similarity(structure_set, _features(json.loads(row[3])))
            if similarity < min_similarity:
                continue
            candidates.append(AnalogyCandidate(
                row[0], row[1], row[2], similarity,
                tuple(json.loads(row[4])), float(row[5]), negative,
            ))
        return sorted(candidates, key=lambda item: (-item.similarity, -item.confidence, item.abstraction_id))[:limit]

    def validate_candidate(self, candidate: AnalogyCandidate, *, evidence_ids: Iterable[str]) -> bool:
        ids = tuple(sorted({_clean(item, "evidence_id") for item in evidence_ids}))
        if not ids or set(ids) & set(candidate.evidence_ids):
            return False
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT 1 FROM generalization_abstractions WHERE project=? AND id=?",
                (self.project, candidate.abstraction_id),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "INSERT OR IGNORE INTO generalization_validations VALUES(?,?,?,?)",
                (self.project, candidate.abstraction_id, json.dumps(ids), datetime.now(timezone.utc).isoformat()),
            )
        return True


__all__ = ["Abstraction", "AnalogyCandidate", "GeneralizationEngine"]
