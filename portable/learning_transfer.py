"""Evidence-gated learning transfer and durable memory consolidation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .persistent_memory import PersistentMemory


def _clean(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _signature(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_clean(value, "structure_signature") for value in values)


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass(frozen=True)
class LearningExperience:
    id: str
    source_project: str
    task_family: str
    capability: str
    outcome: str
    detail: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    verified: bool = False
    structure_signature: tuple[str, ...] = ()
    negative_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferCandidate:
    detail: str
    capability: str
    task_family: str
    source_projects: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    confidence: float
    similarity: float = 1.0
    negative_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailureConstraint:
    problem: str
    dont: str
    evidence_ids: tuple[str, ...]
    confidence: float
    source_project: str


@dataclass(frozen=True)
class ConsolidationReceipt:
    task_family: str
    capability: str
    detail: str
    source_projects: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    memory_id: str


class LearningTransfer:
    """Record, validate, transfer, and consolidate verified experience across projects."""

    def __init__(self, memory: PersistentMemory, target_project: str) -> None:
        self.memory = memory
        self.target_project = _clean(target_project, "target_project")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS learning_experiences(
                id TEXT PRIMARY KEY,
                source_project TEXT NOT NULL,
                task_family TEXT NOT NULL,
                capability TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT NOT NULL,
                evidence_ids TEXT NOT NULL,
                confidence REAL NOT NULL,
                verified INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_learning_lookup
                ON learning_experiences(task_family, capability, verified, outcome)""")
            db.execute("""CREATE TABLE IF NOT EXISTS learning_transfer_signatures(
                experience_id TEXT PRIMARY KEY,
                structure_signature TEXT NOT NULL,
                negative_conditions TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS learning_transfer_validations(
                project TEXT NOT NULL, validation_id TEXT NOT NULL, detail TEXT NOT NULL,
                source_projects TEXT NOT NULL, similarity REAL NOT NULL, success INTEGER NOT NULL,
                negative_transfer INTEGER NOT NULL, evidence_ids TEXT NOT NULL,
                detail_text TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project, validation_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transfer_validation_lookup ON learning_transfer_validations(project, detail, negative_transfer)")
            db.execute("""CREATE TABLE IF NOT EXISTS failure_constraints(
                id TEXT PRIMARY KEY, project TEXT NOT NULL, problem TEXT NOT NULL,
                dont TEXT NOT NULL, evidence_ids TEXT NOT NULL, confidence REAL NOT NULL,
                created_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_failure_constraints_lookup ON failure_constraints(project, problem, created_at)")

    def record(self, experience: LearningExperience) -> None:
        if not isinstance(experience, LearningExperience):
            raise ValueError("experience must be a LearningExperience")
        for value, field in (
            (experience.id, "id"), (experience.source_project, "source_project"),
            (experience.task_family, "task_family"), (experience.capability, "capability"),
            (experience.outcome, "outcome"), (experience.detail, "detail"),
        ):
            _clean(value, field)
        if not 0 <= experience.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        evidence = tuple(sorted({_clean(item, "evidence_id") for item in experience.evidence_ids}))
        structure = tuple(sorted(_signature(experience.structure_signature)))
        negatives = tuple(sorted(_signature(experience.negative_conditions)))
        if experience.verified and not evidence:
            raise ValueError("verified learning requires evidence_ids")
        created_at = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connect() as db:
            existing = db.execute("SELECT source_project,task_family,capability,outcome,detail,evidence_ids,confidence,verified FROM learning_experiences WHERE id=?", (experience.id,)).fetchone()
            if existing:
                incoming = (experience.source_project, experience.task_family, experience.capability, experience.outcome,
                            experience.detail, json.dumps(evidence), experience.confidence, int(experience.verified))
                if existing != incoming:
                    raise ValueError(f"learning experience id already exists with different content: {experience.id}")
                signature_row = db.execute(
                    "SELECT structure_signature,negative_conditions FROM learning_transfer_signatures WHERE experience_id=?",
                    (experience.id,),
                ).fetchone()
                incoming_signature = (json.dumps(structure), json.dumps(negatives))
                if signature_row is not None and signature_row != incoming_signature:
                    raise ValueError(f"learning experience id already exists with different transfer metadata: {experience.id}")
                db.execute(
                    "INSERT OR IGNORE INTO learning_transfer_signatures VALUES(?,?,?)",
                    (experience.id, json.dumps(structure), json.dumps(negatives)),
                )
                return
            db.execute("INSERT INTO learning_experiences VALUES(?,?,?,?,?,?,?,?,?,?)", (
                experience.id, experience.source_project, experience.task_family, experience.capability,
                experience.outcome, experience.detail, json.dumps(evidence), experience.confidence,
                int(experience.verified), created_at,
            ))
            db.execute("INSERT INTO learning_transfer_signatures VALUES(?,?,?)",
                       (experience.id, json.dumps(structure), json.dumps(negatives)))

    def record_failure(self, *, problem: str, dont: str, evidence_ids: tuple[str, ...],
                       confidence: float = 0.9) -> FailureConstraint:
        problem = _clean(problem, "problem")
        dont = _clean(dont, "dont")
        evidence = tuple(sorted({_clean(x, "evidence_id") for x in evidence_ids}))
        if not evidence:
            raise ValueError("failure constraints require evidence")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        import hashlib
        from datetime import datetime, timezone
        key = hashlib.sha256(("\0".join((self.target_project, problem, dont, "|".join(evidence)))).encode()).hexdigest()
        constraint = FailureConstraint(problem, dont, evidence, confidence, self.target_project)
        with self.memory._lock, self.memory._connect() as db:
            db.execute("INSERT OR IGNORE INTO failure_constraints VALUES(?,?,?,?,?,?,?)",
                       (key, self.target_project, problem, dont, json.dumps(evidence),
                        confidence, datetime.now(timezone.utc).isoformat()))
        self.memory.remember(
            self.target_project, "failure_dont",
            f"Do not repeat this failed approach for {problem}: {dont}",
            confidence=confidence, verified=True, approved=True,
        )
        return constraint

    def failure_constraints(self, problem: str, *, limit: int = 10) -> list[FailureConstraint]:
        problem = _clean(problem, "problem")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("""SELECT problem,dont,evidence_ids,confidence,project
                FROM failure_constraints WHERE project=? AND problem LIKE ?
                ORDER BY confidence DESC,created_at DESC LIMIT ?""",
                (self.target_project, "%" + problem + "%", limit)).fetchall()
        return [FailureConstraint(str(p), str(d), tuple(json.loads(e or "[]")), float(c), str(project))
                for p,d,e,c,project in rows]

    def _is_blocked(self, db, detail: str) -> bool:
        return db.execute(
            "SELECT 1 FROM learning_transfer_validations WHERE project=? AND detail=? AND negative_transfer=1 LIMIT 1",
            (self.target_project, detail),
        ).fetchone() is not None

    def transfer(self, task_family: str, capability: str, *, limit: int = 10) -> list[TransferCandidate]:
        task_family = _clean(task_family, "task_family")
        capability = _clean(capability, "capability")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("""SELECT detail,source_project,evidence_ids,confidence
                FROM learning_experiences
                WHERE task_family=? AND capability=? AND outcome='worked' AND verified=1
                  AND source_project<>?
                ORDER BY confidence DESC, created_at DESC, id""", (task_family, capability, self.target_project)).fetchall()
            groups: dict[str, dict[str, object]] = {}
            for detail, source_project, evidence_json, confidence in rows:
                if self._is_blocked(db, str(detail)):
                    continue
                item = groups.setdefault(str(detail), {"projects": set(), "evidence": set(), "confidence": 0.0})
                item["projects"].add(str(source_project))
                item["evidence"].update(json.loads(evidence_json or "[]"))
                item["confidence"] = max(float(item["confidence"]), float(confidence))
        candidates = [
            TransferCandidate(detail, capability, task_family, tuple(sorted(item["projects"])),
                              tuple(sorted(item["evidence"])), float(item["confidence"]))
            for detail, item in groups.items()
        ]
        return sorted(candidates, key=lambda x: (-len(x.source_projects), -x.confidence, x.detail))[:limit]

    def transfer_structural(self, task_family: str, capability: str, structure_signature: tuple[str, ...], *,
                            target_conditions: tuple[str, ...] = (), min_similarity: float = 0.25,
                            limit: int = 10) -> list[TransferCandidate]:
        task_family = _clean(task_family, "task_family")
        capability = _clean(capability, "capability")
        target = _signature(tuple(structure_signature))
        conditions = _signature(tuple(target_conditions))
        if not 0 <= min_similarity <= 1:
            raise ValueError("min_similarity must be between 0 and 1")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("""SELECT e.id,e.detail,e.source_project,e.evidence_ids,e.confidence,
                                       s.structure_signature,s.negative_conditions
                FROM learning_experiences e
                JOIN learning_transfer_signatures s ON s.experience_id=e.id
                WHERE e.task_family=? AND e.capability=? AND e.outcome='worked' AND e.verified=1
                  AND e.source_project<>?
                ORDER BY e.confidence DESC, e.created_at DESC, e.id""",
                              (task_family, capability, self.target_project)).fetchall()
            groups: dict[tuple[str, str], dict[str, object]] = {}
            for experience_id, detail, source_project, evidence_json, confidence, structure_json, negative_json in rows:
                if self._is_blocked(db, str(detail)):
                    continue
                negatives = _signature(tuple(json.loads(negative_json or "[]")))
                if negatives & conditions:
                    continue
                source_structure = _signature(tuple(json.loads(structure_json or "[]")))
                similarity = _similarity(target, source_structure)
                if similarity < min_similarity:
                    continue
                key = (str(detail), json.dumps(sorted(source_structure)))
                item = groups.setdefault(key, {
                    "detail": str(detail), "projects": set(), "evidence": set(), "confidence": 0.0,
                    "similarity": similarity, "negative": negatives,
                })
                item["projects"].add(str(source_project))
                item["evidence"].update(json.loads(evidence_json or "[]"))
                item["confidence"] = max(float(item["confidence"]), float(confidence))
                item["similarity"] = max(float(item["similarity"]), similarity)
        candidates = [
            TransferCandidate(str(item["detail"]), capability, task_family,
                              tuple(sorted(item["projects"])), tuple(sorted(item["evidence"])),
                              float(item["confidence"]), float(item["similarity"]),
                              tuple(sorted(item["negative"])))
            for item in groups.values()
        ]
        return sorted(candidates, key=lambda x: (-x.similarity, -len(x.source_projects), -x.confidence, x.detail))[:limit]

    def consolidate(self, task_family: str, capability: str, *, min_projects: int = 2) -> ConsolidationReceipt | None:
        task_family = _clean(task_family, "task_family")
        capability = _clean(capability, "capability")
        if min_projects < 2:
            raise ValueError("min_projects must be at least 2")
        candidates = self.transfer(task_family, capability, limit=100)
        candidates = [candidate for candidate in candidates if len(candidate.source_projects) >= min_projects]
        if not candidates:
            return None
        candidate = candidates[0]
        text = (
            f"Transferable pattern for {task_family}/{capability}: {candidate.detail}. "
            f"Independently reproduced across {len(candidate.source_projects)} projects."
        )
        record = self.memory.remember(
            self.target_project, "procedural_learning", text,
            confidence=candidate.confidence, verified=True, approved=True,
        )
        if record is None:
            return None
        return ConsolidationReceipt(task_family, capability, candidate.detail,
                                    candidate.source_projects, candidate.evidence_ids, record.id)


__all__ = ["ConsolidationReceipt", "FailureConstraint", "LearningExperience", "LearningTransfer", "TransferCandidate"]
