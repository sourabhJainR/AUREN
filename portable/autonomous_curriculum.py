"""Autonomous, bounded curriculum discovery for informative generalization probes.

The selector chooses which candidate task families and conditions are most
informative from uncertainty, novelty, failure history, and prior coverage.
It does not execute experiments or grant execution authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

from .persistent_memory import PersistentMemory


def _clean(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


@dataclass(frozen=True)
class CurriculumCandidate:
    task_family: str
    condition: str
    uncertainty: float
    novelty: float
    failure_rate: float
    historical_count: int
    expected_information_gain: float
    priority: float


@dataclass(frozen=True)
class CurriculumDecision:
    decision_id: str
    capability: str
    selected: tuple[CurriculumCandidate, ...]
    candidates: tuple[CurriculumCandidate, ...]
    budget: int
    rationale: tuple[str, ...]
    evidence_id: str


class AutonomousCurriculumDiscovery:
    """Select a small, deterministic curriculum from a bounded candidate pool."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        self.memory = memory
        self.project = _clean(project, "project")
        with self.memory._lock, self.memory._connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS autonomous_curriculum_observations(
                    project TEXT NOT NULL, capability TEXT NOT NULL,
                    task_family TEXT NOT NULL, condition TEXT NOT NULL,
                    score REAL NOT NULL, verified INTEGER NOT NULL,
                    created_at TEXT NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS autonomous_curriculum_decisions(
                    project TEXT NOT NULL, decision_id TEXT NOT NULL,
                    capability TEXT NOT NULL, selected TEXT NOT NULL,
                    rationale TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project, decision_id))"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_autonomous_curriculum_history "
                "ON autonomous_curriculum_observations(project, capability, task_family, condition)"
            )

    def record_outcome(
        self,
        capability: str,
        task_family: str,
        condition: str,
        *,
        score: float,
        verified: bool = True,
    ) -> None:
        capability = _clean(capability, "capability")
        task_family = _clean(task_family, "task_family")
        condition = _clean(condition, "condition")
        if not 0 <= score <= 1:
            raise ValueError("score must be between 0 and 1")
        now = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connection() as db:
            db.execute(
                "INSERT INTO autonomous_curriculum_observations VALUES(?,?,?,?,?,?,?)",
                (self.project, capability, task_family, condition, score, int(bool(verified)), now),
            )

    def _history(self, capability: str, task_family: str, condition: str) -> tuple[int, float, float]:
        with self.memory._lock, self.memory._connection() as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(AVG(score),0), "
                "COALESCE(AVG(CASE WHEN score < 0.70 THEN 1.0 ELSE 0.0 END),0) "
                "FROM autonomous_curriculum_observations "
                "WHERE project=? AND capability=? AND task_family=? AND condition=? AND verified=1",
                (self.project, capability, task_family, condition),
            ).fetchone()
        count = int(row[0]) if row else 0
        mean = float(row[1]) if row else 0.0
        failures = float(row[2]) if row else 0.0
        return count, mean, failures

    def discover(
        self,
        capability: str,
        task_families: Sequence[str],
        *,
        uncertainty: Mapping[str, float] | None = None,
        conditions: Sequence[str] = ("novel-input", "constraint-shift", "composition"),
        budget: int = 4,
    ) -> CurriculumDecision:
        capability = _clean(capability, "capability")
        families = tuple(dict.fromkeys(_clean(x, "task_family") for x in task_families))
        conditions = tuple(dict.fromkeys(_clean(x, "condition") for x in conditions))
        if len(families) < 2:
            raise ValueError("at least two task families are required")
        if not conditions:
            raise ValueError("at least one condition is required")
        if budget < 2:
            raise ValueError("budget must be at least 2")
        budget = min(budget, len(families) * len(conditions))
        uncertainty = uncertainty or {}
        candidates = []
        for family in families:
            u = float(uncertainty.get(family, 0.5))
            if not 0 <= u <= 1:
                raise ValueError(f"uncertainty for {family} must be between 0 and 1")
            for condition in conditions:
                count, mean, failure_rate = self._history(capability, family, condition)
                raw = hashlib.sha256(f"{capability}|{family}|{condition}".encode()).hexdigest()
                novelty = 0.5 + int(raw[:8], 16) / 0xFFFFFFFF * 0.5
                coverage_gap = 1.0 / (1.0 + count)
                expected_gain = min(1.0, u * (0.55 * novelty + 0.45 * coverage_gap))
                success_penalty = 0.55 + 0.45 * (1.0 - mean) if count else 1.0
                priority = expected_gain * (1.0 + 0.75 * failure_rate) * success_penalty
                candidates.append(CurriculumCandidate(
                    family, condition, u, novelty, failure_rate, count,
                    expected_gain, priority,
                ))
        candidates.sort(key=lambda c: (-c.priority, -c.expected_information_gain, c.task_family, c.condition))
        selected = []
        covered = set()
        # Preserve family diversity before filling the remaining budget.
        for candidate in candidates:
            if candidate.task_family not in covered:
                selected.append(candidate)
                covered.add(candidate.task_family)
            if len(selected) == min(budget, len(families)):
                break
        selected_ids = {(x.task_family, x.condition) for x in selected}
        selected.extend(
            x for x in candidates if (x.task_family, x.condition) not in selected_ids
        )
        selected = tuple(selected[:budget])
        rationale = (
            "prioritize uncertainty and expected information gain",
            "increase priority for unresolved failure history",
            "prefer under-tested candidates while preserving task-family diversity",
            "reuse historical outcomes without granting execution authority",
        )
        digest = hashlib.sha256(
            json.dumps([(x.task_family, x.condition) for x in selected], sort_keys=True).encode()
        ).hexdigest()[:16]
        decision_id = f"curriculum-{digest}"
        evidence_id = f"curriculum-evidence-{digest}"
        now = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO autonomous_curriculum_decisions VALUES(?,?,?,?,?,?)",
                (self.project, decision_id, capability,
                 json.dumps([(x.task_family, x.condition) for x in selected]),
                 json.dumps(rationale), now),
            )
        return CurriculumDecision(
            decision_id, capability, selected, tuple(candidates), budget, rationale, evidence_id
        )


__all__ = ["CurriculumCandidate", "CurriculumDecision", "AutonomousCurriculumDiscovery"]
