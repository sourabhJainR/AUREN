"""Evidence-gated capability acquisition with bounded practice and graduation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from .persistent_memory import PersistentMemory
from .skill_graph import SkillGraph, SkillNode


@dataclass(frozen=True)
class CapabilityNeed:
    task_family: str
    capability: str
    missing_count: int
    evidence_ids: tuple[str, ...]
    verified: bool

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.task_family, self.capability)):
            raise ValueError("task_family and capability must be non-empty")
        if self.missing_count < 1:
            raise ValueError("missing_count must be positive")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_ids):
            raise ValueError("evidence_ids must contain non-empty strings")


@dataclass(frozen=True)
class CapabilityProposal:
    id: str
    task_family: str
    capability: str
    missing_count: int
    evidence_ids: tuple[str, ...]
    executable: bool = False


@dataclass(frozen=True)
class ValidationEvidence:
    evidence_ids: tuple[str, ...]
    tests_passed: bool
    safety_reviewed: bool
    approved: bool = False


@dataclass(frozen=True)
class ValidationReceipt:
    proposal_id: str
    accepted: bool
    executable: bool
    evidence_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PracticeResult:
    proposal_id: str
    attempt_id: str
    accepted: bool
    tests_passed: bool
    safety_reviewed: bool
    detail: str = ""
    error: str | None = None


@dataclass(frozen=True)
class GraduationReceipt:
    proposal_id: str
    accepted: bool
    skill_name: str
    evidence_ids: tuple[str, ...]
    practice_attempts: tuple[str, ...]
    reasons: tuple[str, ...]


class CapabilityAcquirer:
    """Turn repeated verified gaps into bounded practice and validated skills."""

    def __init__(self, memory: PersistentMemory, project: str, *, min_missing: int = 3) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if min_missing < 2:
            raise ValueError("min_missing must be at least 2")
        self.memory = memory
        self.project = project.strip()
        self.min_missing = min_missing
        with self.memory._lock, self.memory._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS capability_proposals(
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                task_family TEXT NOT NULL,
                capability TEXT NOT NULL,
                missing_count INTEGER NOT NULL,
                evidence_ids TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS capability_practice(
                proposal_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                tests_passed INTEGER NOT NULL,
                safety_reviewed INTEGER NOT NULL,
                detail TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(proposal_id,attempt_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS capability_graduations(
                proposal_id TEXT PRIMARY KEY,
                skill_name TEXT NOT NULL,
                evidence_ids TEXT NOT NULL,
                practice_attempts TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def propose(self, need: CapabilityNeed) -> CapabilityProposal | None:
        evidence = tuple(sorted(set(need.evidence_ids)))
        if not need.verified or need.missing_count < self.min_missing or len(evidence) < self.min_missing:
            return None
        payload = f"{self.project}|{need.task_family.strip()}|{need.capability.strip()}|{need.missing_count}|{json.dumps(evidence)}"
        proposal_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        proposal = CapabilityProposal(proposal_id, need.task_family.strip(), need.capability.strip(), need.missing_count, evidence)
        with self.memory._lock, self.memory._connection() as db:
            db.execute("INSERT OR IGNORE INTO capability_proposals VALUES(?,?,?,?,?,?,?)",
                       (proposal.id, self.project, proposal.task_family, proposal.capability, proposal.missing_count,
                        json.dumps(proposal.evidence_ids), datetime.now(timezone.utc).isoformat()))
        return proposal

    def validate(self, proposal_id: str, evidence: ValidationEvidence) -> ValidationReceipt:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required")
        if not isinstance(evidence, ValidationEvidence):
            raise ValueError("evidence must be ValidationEvidence")
        validation_ids = tuple(sorted(set(evidence.evidence_ids)))
        if any(not isinstance(value, str) or not value.strip() for value in validation_ids):
            raise ValueError("validation evidence must contain non-empty ids")
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute("SELECT id,evidence_ids FROM capability_proposals WHERE id=? AND project=?", (proposal_id, self.project)).fetchone()
        if row is None:
            raise KeyError(f"unknown capability proposal: {proposal_id}")
        reasons: list[str] = []
        if not validation_ids:
            reasons.append("missing validation evidence")
        if not evidence.tests_passed:
            reasons.append("validation tests did not pass")
        if not evidence.safety_reviewed:
            reasons.append("safety review is missing")
        if self.memory.require_approval and not evidence.approved:
            reasons.append("approval is required")
        accepted = not reasons
        return ValidationReceipt(proposal_id, accepted, False, validation_ids, tuple(reasons))

    def practice(self, proposal_id: str, runner: Callable[[], Any], *, max_detail_chars: int = 512) -> PracticeResult:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required")
        if not callable(runner):
            raise TypeError("runner must be callable")
        if max_detail_chars < 1:
            raise ValueError("max_detail_chars must be positive")
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute("SELECT 1 FROM capability_proposals WHERE id=? AND project=?", (proposal_id, self.project)).fetchone()
        if row is None:
            raise KeyError(f"unknown capability proposal: {proposal_id}")
        attempt_id = uuid4().hex
        accepted = tests_passed = safety_reviewed = False
        detail = ""
        error: str | None = None
        try:
            outcome = runner()
            if not isinstance(outcome, dict):
                raise TypeError("practice runner must return a dict")
            tests_passed = bool(outcome.get("tests_passed", False))
            safety_reviewed = bool(outcome.get("safety_reviewed", False))
            detail = str(outcome.get("detail", ""))[:max_detail_chars]
            accepted = tests_passed and safety_reviewed
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:max_detail_chars]
        result = PracticeResult(proposal_id, attempt_id, accepted, tests_passed, safety_reviewed, detail, error)
        with self.memory._lock, self.memory._connection() as db:
            db.execute("INSERT INTO capability_practice VALUES(?,?,?,?,?,?,?,?)",
                       (proposal_id, attempt_id, int(accepted), int(tests_passed), int(safety_reviewed), detail,
                        error, datetime.now(timezone.utc).isoformat()))
        return result

    def graduate(self, proposal_id: str, results: tuple[PracticeResult, ...], *, evidence_ids: tuple[str, ...]) -> GraduationReceipt:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required")
        evidence = tuple(sorted(set(evidence_ids)))
        with self.memory._lock, self.memory._connection() as db:
            proposal = db.execute("SELECT task_family,capability FROM capability_proposals WHERE id=? AND project=?", (proposal_id, self.project)).fetchone()
            if proposal is None:
                raise KeyError(f"unknown capability proposal: {proposal_id}")
            supplied_ids = tuple(dict.fromkeys(result.attempt_id for result in results if result.proposal_id == proposal_id and result.accepted))
            if supplied_ids:
                placeholders = ",".join("?" for _ in supplied_ids)
                stored = db.execute(
                    f"SELECT attempt_id FROM capability_practice WHERE proposal_id=? AND accepted=1 AND attempt_id IN ({placeholders})",
                    (proposal_id, *supplied_ids),
                ).fetchall()
                persisted_ids = {row[0] for row in stored}
            else:
                persisted_ids = set()
        reasons: list[str] = []
        accepted_results = tuple(result for result in results if result.proposal_id == proposal_id and result.accepted)
        attempt_ids = tuple(dict.fromkeys(result.attempt_id for result in accepted_results))
        if len(accepted_results) < 2:
            reasons.append("at least two accepted practice attempts are required")
        if len(attempt_ids) != len(accepted_results):
            reasons.append("practice attempt ids must be distinct")
        if len(persisted_ids) != len(attempt_ids):
            reasons.append("practice attempts must match persisted accepted attempts")
        if len(evidence) < 2:
            reasons.append("at least two graduation evidence ids are required")
        if reasons:
            return GraduationReceipt(proposal_id, False, proposal[1], evidence, attempt_ids, tuple(reasons))
        skill_graph = SkillGraph(self.memory, self.project)
        skill_graph.upsert(SkillNode(
            proposal[1], "acquired", frozenset(), frozenset(evidence), frozenset(), frozenset({proposal[0]}), True,
        ))
        with self.memory._lock, self.memory._connection() as db:
            db.execute("INSERT OR REPLACE INTO capability_graduations VALUES(?,?,?,?,?)",
                       (proposal_id, proposal[1], json.dumps(evidence), json.dumps(attempt_ids), datetime.now(timezone.utc).isoformat()))
        return GraduationReceipt(proposal_id, True, proposal[1], evidence, attempt_ids, ())


__all__ = [
    "CapabilityAcquirer", "CapabilityNeed", "CapabilityProposal", "GraduationReceipt", "PracticeResult",
    "ValidationEvidence", "ValidationReceipt",
]
