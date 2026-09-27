"""SkillOpt-inspired governed skill evolution for AER.

This additive layer trains a skill artifact from verified experience. It does not
own execution, permissions, evidence authority, or final promotion policy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from .persistent_memory import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SkillEdit:
    """A bounded add, replace, or delete operation."""
    op: str
    content: str = ""
    anchor: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.op not in {"add", "replace", "delete"}:
            raise ValueError("op must be add, replace or delete")
        if self.op in {"replace", "delete"} and not self.anchor.strip():
            raise ValueError(f"{self.op} requires an anchor")
        if self.op == "add" and not self.content.strip():
            raise ValueError("add requires content")
        if len(self.content) > 4000 or len(self.anchor) > 1000 or len(self.rationale) > 2000:
            raise ValueError("skill edit exceeds bounded size")


@dataclass(frozen=True)
class SkillScore:
    hard: float
    soft: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.hard <= 1 or not 0 <= self.soft <= 1:
            raise ValueError("skill scores must be between 0 and 1")

    def value(self, metric: str = "hard", mixed_weight: float = 0.5) -> float:
        if metric == "hard":
            return self.hard
        if metric == "soft":
            return self.soft
        if metric == "mixed":
            weight = max(0.0, min(1.0, float(mixed_weight)))
            return (1.0 - weight) * self.hard + weight * self.soft
        raise ValueError("metric must be hard, soft or mixed")


@dataclass(frozen=True)
class SkillOptimizationResult:
    accepted: bool
    baseline: SkillScore
    candidate: SkillScore
    skill: str
    applied_edits: tuple[SkillEdit, ...]
    rejected_edits: tuple[SkillEdit, ...]
    unmatched_edits: tuple[SkillEdit, ...]
    reason: str
    holdout_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    digest: str


class SkillOptimizer:
    """One offline SkillOpt-style epoch under AER gates.

    score(skill, task_ids) must return a deterministic SkillScore. This class
    never executes production work and never changes host policy.
    """

    def __init__(
        self,
        memory: PersistentMemory,
        project: str,
        *,
        edit_budget: int = 4,
        min_improvement: float = 0.0,
        metric: str = "mixed",
        mixed_weight: float = 0.5,
    ) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not project.strip():
            raise ValueError("project is required")
        if edit_budget < 1:
            raise ValueError("edit_budget must be positive")
        if min_improvement < 0:
            raise ValueError("min_improvement must be non-negative")
        if metric not in {"hard", "soft", "mixed"}:
            raise ValueError("metric must be hard, soft or mixed")
        self.memory = memory
        self.project = project.strip()
        self.edit_budget = edit_budget
        self.min_improvement = float(min_improvement)
        self.metric = metric
        self.mixed_weight = float(mixed_weight)
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS skill_optimization_edits(
                project TEXT NOT NULL, epoch_id TEXT NOT NULL, target TEXT NOT NULL,
                op TEXT NOT NULL, content TEXT NOT NULL, anchor TEXT NOT NULL,
                rationale TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project,epoch_id,target,op,anchor,content))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skill_optimization_evidence(
                project TEXT NOT NULL, epoch_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                PRIMARY KEY(project,epoch_id,evidence_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skill_optimization_epochs(
                project TEXT NOT NULL, epoch_id TEXT NOT NULL, task_family TEXT NOT NULL,
                baseline_score REAL NOT NULL, candidate_score REAL NOT NULL,
                accepted INTEGER NOT NULL, holdout_ids TEXT NOT NULL,
                digest TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project,epoch_id))""")

    @staticmethod
    def apply_edits(skill: str, edits: Sequence[SkillEdit], *, budget: int = 4):
        if budget < 1:
            raise ValueError("budget must be positive")
        current = str(skill)
        applied: list[SkillEdit] = []
        unmatched: list[SkillEdit] = []
        for edit in tuple(edits)[:budget]:
            if edit.op == "add":
                marker = edit.anchor.strip()
                if marker and marker not in current:
                    unmatched.append(edit)
                    continue
                if edit.content.strip() in current:
                    unmatched.append(edit)
                    continue
                if marker:
                    current = current.replace(marker, marker + "\n" + edit.content.strip(), 1)
                else:
                    current = current.rstrip() + "\n" + edit.content.strip() + "\n"
                applied.append(edit)
            elif edit.op == "replace":
                if edit.anchor not in current:
                    unmatched.append(edit)
                    continue
                current = current.replace(edit.anchor, edit.content, 1)
                applied.append(edit)
            else:
                if edit.anchor not in current:
                    unmatched.append(edit)
                    continue
                current = current.replace(edit.anchor, "", 1)
                applied.append(edit)
        return current, tuple(applied), tuple(unmatched)

    def _record(self, epoch_id: str, task_family: str, baseline: SkillScore,
                candidate: SkillScore, accepted: bool, holdout_ids: Sequence[str],
                edits: Iterable[SkillEdit], status: str) -> None:
        now = _utc()
        with self.memory._lock, self.memory._connect() as db:
            for edit in edits:
                db.execute(
                    "INSERT OR IGNORE INTO skill_optimization_edits VALUES(?,?,?,?,?,?,?,?,?)",
                    (self.project, epoch_id, "skill", edit.op, edit.content, edit.anchor,
                     edit.rationale, status, now),
                )
            payload = {
                "epoch_id": epoch_id, "task_family": task_family,
                "baseline": baseline.__dict__, "candidate": candidate.__dict__,
                "accepted": accepted, "holdout_ids": sorted(set(holdout_ids)),
            }
            for evidence_id in sorted(set(evidence_ids)):
                db.execute(
                    "INSERT OR IGNORE INTO skill_optimization_evidence VALUES(?,?,?)",
                    (self.project, epoch_id, evidence_id),
                )
            db.execute(
                "INSERT OR REPLACE INTO skill_optimization_epochs VALUES(?,?,?,?,?,?,?,?,?)",
                (self.project, epoch_id, task_family,
                 baseline.value(self.metric, self.mixed_weight),
                 candidate.value(self.metric, self.mixed_weight), int(accepted),
                 json.dumps(sorted(set(holdout_ids))), _digest(payload), now),
            )

    def rejected(self, *, limit: int = 100) -> tuple[SkillEdit, ...]:
        if limit < 1:
            return ()
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                """SELECT op,content,anchor,rationale FROM skill_optimization_edits
                   WHERE project=? AND status='rejected'
                   ORDER BY created_at DESC LIMIT ?""",
                (self.project, limit),
            ).fetchall()
        return tuple(SkillEdit(row[0], row[1], row[2], row[3]) for row in rows)

    def epoch(
        self,
        *,
        task_family: str,
        skill: str,
        proposals: Sequence[SkillEdit],
        train_ids: Sequence[str],
        holdout_ids: Sequence[str],
        evidence_ids: Sequence[str],
        score: Callable[[str, Sequence[str]], SkillScore],
    ) -> SkillOptimizationResult:
        """Propose -> bounded edit -> disjoint held-out validation."""
        if not task_family.strip():
            raise ValueError("task_family is required")
        if not train_ids or not holdout_ids:
            raise ValueError("train and holdout tasks are required")
        clean_evidence = tuple(sorted({str(x).strip() for x in evidence_ids if str(x).strip()}))
        if not clean_evidence:
            raise ValueError("verified skill optimization requires evidence_ids")
        train_set, holdout_set = set(train_ids), set(holdout_ids)
        if train_set & holdout_set:
            raise ValueError("train and holdout sets must be disjoint")
        selected = tuple(proposals[: self.edit_budget])
        baseline = score(skill, tuple(sorted(holdout_set)))
        candidate_skill, applied, unmatched = self.apply_edits(
            skill, selected, budget=self.edit_budget
        )
        if not applied:
            candidate = baseline
            reason = "no applicable bounded edits"
            accepted = False
            rejected = ()
        else:
            candidate = score(candidate_skill, tuple(sorted(holdout_set)))
            improvement = (
                candidate.value(self.metric, self.mixed_weight)
                - baseline.value(self.metric, self.mixed_weight)
            )
            accepted = improvement > self.min_improvement
            reason = (
                "held-out score improved"
                if accepted else "held-out score did not strictly improve"
            )
            rejected = () if accepted else applied
        epoch_id = _digest({
            "project": self.project, "task_family": task_family,
            "skill": _digest(skill), "proposals": [e.__dict__ for e in selected],
            "train_ids": sorted(train_set), "holdout_ids": sorted(holdout_set), "evidence_ids": clean_evidence,
        })[:24]
        if accepted:
            self._record(epoch_id, task_family, baseline, candidate, True,
                         holdout_ids, clean_evidence, applied, "accepted")
        else:
            self._record(epoch_id, task_family, baseline, candidate, False,
                         holdout_ids, clean_evidence, rejected, "rejected")
        return SkillOptimizationResult(
            accepted, baseline, candidate,
            candidate_skill if accepted else skill,
            applied if accepted else (), rejected, unmatched, reason,
            tuple(sorted(holdout_set)), clean_evidence, epoch_id,
        )

    def slow_update(self, current: str, accepted: str, *, rate: float = 0.25) -> str:
        """Conservative deterministic slow update for text state."""
        if not 0 <= rate <= 1:
            raise ValueError("rate must be between 0 and 1")
        if rate == 0:
            return current
        current_lines = [line for line in current.splitlines() if line.strip()]
        accepted_lines = [line for line in accepted.splitlines() if line.strip()]
        additions = [line for line in accepted_lines if line not in current_lines]
        take = max(0, min(len(additions), int(round(len(additions) * rate))))
        if take == 0:
            return current
        return current.rstrip() + "\n" + "\n".join(additions[:take]) + "\n"


__all__ = ["SkillEdit", "SkillScore", "SkillOptimizationResult", "SkillOptimizer"]
