"""Deferred learning lane for empirical workstyle and quality adaptation.

The active worker records compact outcomes and retrieves already-promoted
workstyle guidance. Consolidation, profile updates, and Dream Memory execution
happen only when an explicit maintenance caller drains this lane.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from .cognitive_learning import CognitiveLearningLoop
from .dream_memory import DreamMemory
from .hypothesis_engine import BeliefEvidence
from .persistent_memory import PersistentMemory
from .skill_optimization import SkillEdit, SkillOptimizationResult, SkillOptimizer, SkillScore


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class WorkStyleProfile:
    project: str
    preferred_detail: str
    verification_emphasis: str
    iteration_target: float
    confidence: float
    observations: int
    average_quality: float = 0.0


@dataclass(frozen=True)
class DeferredLearningJob:
    job_id: str
    project: str
    task_id: str
    kind: str
    payload: dict[str, object]
    status: str


class AdaptiveLearningStore:
    """Persist bounded worker outcomes and process them outside execution."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_jobs: int = 100_000,
                 dream_root: Path | str | None = None) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not project or not project.strip():
            raise ValueError("project is required")
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        self.memory = memory
        self.project = project.strip()
        self.max_jobs = max_jobs
        self.dream_root = Path(dream_root).expanduser().resolve() if dream_root is not None else None
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS deferred_learning_jobs(
                project TEXT NOT NULL, job_id TEXT NOT NULL, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT,
                PRIMARY KEY(project, job_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS adaptive_workstyle_profiles(
                project TEXT PRIMARY KEY, preferred_detail TEXT NOT NULL,
                verification_emphasis TEXT NOT NULL, iteration_target REAL NOT NULL,
                confidence REAL NOT NULL, observations INTEGER NOT NULL,
                average_quality REAL NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_deferred_learning_pending ON deferred_learning_jobs(project, status, created_at)")

    def record_outcome(
        self,
        *,
        task_id: str,
        intent: str,
        status: str,
        quality: float = 0.0,
        iterations: int = 1,
        context: str | None = None,
        evidence: Iterable[str] = (),
        verified: bool = True,
        capability: str | None = None,
        belief_evidence: Iterable[BeliefEvidence] = (),
        prediction_id: str | None = None,
        prediction_correct: bool | None = None,
    ) -> DeferredLearningJob:
        if not task_id.strip() or not intent.strip() or not status.strip():
            raise ValueError("task_id, intent and status are required")
        if not 0 <= quality <= 1:
            raise ValueError("quality must be between 0 and 1")
        if iterations < 1:
            raise ValueError("iterations must be positive")
        clean_evidence = tuple(sorted(set(item.strip() for item in evidence if isinstance(item, str) and item.strip())))
        clean_belief_evidence = []
        for item in belief_evidence:
            clean_belief_evidence.append({
                "evidence_id": item.evidence_id,
                "hypothesis_id": item.hypothesis_id,
                "supports": item.supports,
                "detail": item.detail,
                "source": item.source,
                "confidence": item.confidence,
                "observed_at": item.observed_at,
            })
        job_id = uuid4().hex
        payload: dict[str, object] = {
            "intent": intent.strip(),
            "status": status.strip(),
            "quality": float(quality),
            "iterations": int(iterations),
            "context": context.strip() if isinstance(context, str) and context.strip() else None,
            "evidence": clean_evidence,
            "verified": bool(verified),
            "capability": capability,
            "belief_evidence": clean_belief_evidence,
            "prediction_id": prediction_id,
            "prediction_correct": prediction_correct,
        }
        with self.memory._lock, self.memory._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM deferred_learning_jobs WHERE project=? AND status='pending'", (self.project,)).fetchone()[0]
            if count >= self.max_jobs:
                raise ValueError("deferred learning job budget exceeded")
            db.execute(
                "INSERT INTO deferred_learning_jobs VALUES(?,?,?,?,?,?,?,NULL)",
                (self.project, job_id, task_id.strip(), "outcome", json.dumps(payload, sort_keys=True), "pending", _utc()),
            )
        return DeferredLearningJob(job_id, self.project, task_id.strip(), "outcome", payload, "pending")

    def pending(self, *, limit: int = 20) -> tuple[DeferredLearningJob, ...]:
        if limit < 1:
            return ()
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                "SELECT job_id,task_id,kind,payload,status FROM deferred_learning_jobs WHERE project=? AND status='pending' ORDER BY created_at,job_id LIMIT ?",
                (self.project, limit),
            ).fetchall()
        return tuple(DeferredLearningJob(row[0], self.project, row[1], row[2], dict(json.loads(row[3])), row[4]) for row in rows)

    def profile(self) -> WorkStyleProfile:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT preferred_detail,verification_emphasis,iteration_target,confidence,observations,average_quality FROM adaptive_workstyle_profiles WHERE project=?",
                (self.project,),
            ).fetchone()
        if row is None:
            return WorkStyleProfile(self.project, "balanced", "evidence-first", 2.0, 0.0, 0, 0.0)
        return WorkStyleProfile(self.project, row[0], row[1], float(row[2]), float(row[3]), int(row[4]), float(row[5]))

    def process(self, *, limit: int = 20, dream: bool = True) -> tuple[DeferredLearningJob, ...]:
        jobs = self.pending(limit=limit)
        processed: list[DeferredLearningJob] = []
        for job in jobs:
            payload = dict(job.payload)
            if payload.get("verified"):
                try:
                    belief_items = tuple(BeliefEvidence(**item) for item in payload.get("belief_evidence", []))
                    prediction = None
                    if payload.get("prediction_id") is not None and payload.get("prediction_correct") is not None:
                        prediction = SimpleNamespace(
                            prediction_id=str(payload["prediction_id"]),
                            absolute_match=bool(payload["prediction_correct"]),
                        )
                    signal = CognitiveLearningLoop(self.memory, self.project).record(
                        task_id=job.task_id,
                        intent=str(payload["intent"]),
                        status=str(payload["status"]),
                        capability=str(payload["capability"]) if payload.get("capability") else None,
                        evidence=tuple(payload.get("evidence", ())),
                        context=str(payload["context"]) if payload.get("context") else None,
                        belief_evidence=belief_items,
                        prediction_error=prediction,
                    )
                    if signal.persistence_errors:
                        continue
                except Exception:
                    continue
                self._apply_profile(payload)
                if dream and self.dream_root is not None:
                    try:
                        DreamMemory(self.dream_root).dream(job.task_id)
                    except Exception:
                        pass
            with self.memory._lock, self.memory._connect() as db:
                db.execute(
                    "UPDATE deferred_learning_jobs SET status='completed',completed_at=? WHERE project=? AND job_id=? AND status='pending'",
                    (_utc(), self.project, job.job_id),
                )
            processed.append(DeferredLearningJob(job.job_id, job.project, job.task_id, job.kind, job.payload, "completed"))
        return tuple(processed)

    def _apply_profile(self, payload: Mapping[str, object]) -> None:
        quality = float(payload.get("quality", 0.0))
        iterations = int(payload.get("iterations", 1))
        context = str(payload.get("context") or "balanced")
        current = self.profile()
        observations = current.observations + 1
        alpha = 1.0 / observations
        average_quality = current.average_quality + (quality - current.average_quality) * alpha
        iteration_target = current.iteration_target + (iterations - current.iteration_target) * alpha if current.observations else float(iterations)
        confidence = min(0.99, (current.observations + 1) / (current.observations + 3) * max(average_quality, 0.5))
        preferred_detail = self._detail_from_quality(context, quality)
        verification_emphasis = "evidence-first" if quality < 0.9 or iterations > 2 else "verification-efficient"
        with self.memory._lock, self.memory._connect() as db:
            db.execute(
                """INSERT INTO adaptive_workstyle_profiles
                   (project,preferred_detail,verification_emphasis,iteration_target,confidence,observations,average_quality,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(project) DO UPDATE SET
                     preferred_detail=excluded.preferred_detail,
                     verification_emphasis=excluded.verification_emphasis,
                     iteration_target=excluded.iteration_target,
                     confidence=excluded.confidence,
                     observations=excluded.observations,
                     average_quality=excluded.average_quality,
                     updated_at=excluded.updated_at""",
                (self.project, preferred_detail, verification_emphasis, iteration_target, confidence, observations, average_quality, _utc()),
            )

    @staticmethod
    def _detail_from_quality(context: str, quality: float) -> str:
        if context and context not in {"balanced", "None"}:
            return context
        return "concise" if quality >= 0.9 else "evidence-detailed"

    def optimize_skill(
        self,
        *,
        task_family: str,
        skill: str,
        proposals: Iterable[SkillEdit],
        train_ids: Iterable[str],
        holdout_ids: Iterable[str],
        evidence_ids: Iterable[str],
        score: Callable[[str, Sequence[str]], SkillScore],
    ) -> SkillOptimizationResult:
        """Run a validation-gated offline skill epoch.

        This is intentionally a maintenance-lane operation. The active worker
        remains unchanged until the caller takes the accepted artifact through
        AER's normal regression, shadow, canary and promotion gates.
        """
        optimizer = SkillOptimizer(self.memory, self.project)
        return optimizer.epoch(
            task_family=task_family,
            skill=skill,
            proposals=tuple(proposals),
            train_ids=tuple(train_ids),
            holdout_ids=tuple(holdout_ids),
            evidence_ids=tuple(evidence_ids),
            score=score,
        )

    def guidance(self) -> dict[str, object]:
        profile = self.profile()
        payload = {
            "project": profile.project,
            "preferred_detail": profile.preferred_detail,
            "verification_emphasis": profile.verification_emphasis,
            "iteration_target": round(profile.iteration_target, 3),
            "confidence": round(profile.confidence, 3),
            "observations": profile.observations,
            "average_quality": round(profile.average_quality, 3),
        }
        payload["digest"] = _digest(payload)
        return payload


__all__ = ["AdaptiveLearningStore", "DeferredLearningJob", "WorkStyleProfile"]
