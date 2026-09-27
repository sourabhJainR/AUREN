"""Episode-driven SkillOpt evolution with replay and failure memory.

Completed/failed EngineeringEpisodes are converted into bounded skill candidates.
Candidates are replayed on independent corpus cases and remain staged until an
explicit promotion receipt is supplied. The active planner only reads promoted
skills; rejected/staged candidates cannot influence planning.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from .engineering_episode import EngineeringEpisode, EpisodePhase
from .persistent_memory import PersistentMemory
from .skill_optimization import SkillEdit, SkillOptimizationResult, SkillOptimizer, SkillScore


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    task_family: str
    task_id: str
    source: str
    expected_outcome: str = ""

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.task_family.strip() or not self.task_id.strip():
            raise ValueError("replay case id, task family and task id are required")


@dataclass(frozen=True)
class ReplayResult:
    case_ids: tuple[str, ...]
    score: SkillScore
    passed: bool
    digest: str


@dataclass(frozen=True)
class SkillEvolutionResult:
    episode_id: str
    task_family: str
    train_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    proposals: tuple[SkillEdit, ...]
    optimization: SkillOptimizationResult
    replay: ReplayResult
    staged: bool
    planning_eligible: bool


class EpisodeSkillReplayCorpus:
    """Durable replay manifest; execution is injected and never implicit."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        self.memory = memory
        self.project = project.strip()
        if not self.project:
            raise ValueError("project is required")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS skill_replay_cases(
                project TEXT NOT NULL, case_id TEXT NOT NULL, task_family TEXT NOT NULL,
                task_id TEXT NOT NULL, source TEXT NOT NULL, expected_outcome TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(project,case_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skill_evolution_staging(
                project TEXT NOT NULL, epoch_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                task_family TEXT NOT NULL, skill TEXT NOT NULL, candidate_skill TEXT NOT NULL,
                holdout_ids TEXT NOT NULL, replay_digest TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(project,epoch_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skill_evolution_active(
                project TEXT PRIMARY KEY, epoch_id TEXT NOT NULL, skill TEXT NOT NULL,
                replay_digest TEXT NOT NULL, promotion_evidence TEXT NOT NULL, promoted_at TEXT NOT NULL)""")

    def register(self, case: ReplayCase) -> None:
        with self.memory._lock, self.memory._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skill_replay_cases VALUES(?,?,?,?,?,?,?)",
                (self.project, case.case_id, case.task_family, case.task_id, case.source,
                 case.expected_outcome, _utc()),
            )

    def register_episode(self, episode: EngineeringEpisode, *, task_family: str | None = None) -> ReplayCase:
        family = task_family or self.task_family(episode)
        case = ReplayCase(
            case_id=f"episode:{episode.episode_id}",
            task_family=family,
            task_id=episode.task_id,
            source="engineering_episode",
            expected_outcome=episode.outcome,
        )
        self.register(case)
        return case

    def task_family(self, episode: EngineeringEpisode) -> str:
        metadata = dict(episode.metadata)
        return (metadata.get("task_family") or episode.capability or "engineering").strip()

    def holdout(self, *, task_family: str, exclude_task_ids: Iterable[str] = (),
                limit: int = 20) -> tuple[str, ...]:
        excluded = tuple(sorted(set(str(x) for x in exclude_task_ids)))
        placeholders = ",".join("?" for _ in excluded)
        clause = f" AND task_id NOT IN ({placeholders})" if excluded else ""
        args = [self.project, task_family, *excluded, limit]
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                f"""SELECT case_id FROM skill_replay_cases
                    WHERE project=? AND task_family=?{clause}
                    ORDER BY case_id LIMIT ?""", args
            ).fetchall()
        return tuple(row[0] for row in rows)

    def case_ids(self, *, task_family: str | None = None, limit: int = 100) -> tuple[str, ...]:
        with self.memory._lock, self.memory._connect() as db:
            if task_family:
                rows = db.execute(
                    "SELECT case_id FROM skill_replay_cases WHERE project=? AND task_family=? ORDER BY case_id LIMIT ?",
                    (self.project, task_family, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT case_id FROM skill_replay_cases WHERE project=? ORDER BY case_id LIMIT ?",
                    (self.project, limit),
                ).fetchall()
        return tuple(row[0] for row in rows)

    def replay(
        self,
        *,
        skill: str,
        case_ids: Sequence[str],
        evaluator: Callable[[str, str], SkillScore],
        baseline_skill: str | None = None,
    ) -> ReplayResult:
        ids = tuple(sorted(set(case_ids)))
        if not ids:
            raise ValueError("independent replay requires at least one case")
        scores = [evaluator(case_id, skill) for case_id in ids]
        if any(not isinstance(s, SkillScore) for s in scores):
            raise TypeError("replay evaluator must return SkillScore")
        hard = sum(s.hard for s in scores) / len(scores)
        soft = sum(s.soft for s in scores) / len(scores)
        score = SkillScore(hard, soft)
        baseline = score
        if baseline_skill is not None:
            base_scores = [evaluator(case_id, baseline_skill) for case_id in ids]
            baseline = SkillScore(
                sum(s.hard for s in base_scores) / len(base_scores),
                sum(s.soft for s in base_scores) / len(base_scores),
            )
        passed = score.value("mixed") > baseline.value("mixed") if baseline_skill is not None else True
        digest = _digest({"case_ids": ids, "score": score.__dict__, "baseline": baseline.__dict__, "passed": passed})
        return ReplayResult(ids, score, passed, digest)

    def stage(self, *, episode: EngineeringEpisode, task_family: str, current_skill: str,
              candidate_skill: str, holdout_ids: Sequence[str], replay: ReplayResult,
              epoch_id: str) -> None:
        with self.memory._lock, self.memory._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skill_evolution_staging VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project, epoch_id, episode.episode_id, task_family, current_skill,
                 candidate_skill, json.dumps(sorted(set(holdout_ids))), replay.digest,
                 "staged", _utc()),
            )

    def promote(self, *, epoch_id: str, replay: ReplayResult, promotion_evidence: Iterable[str],
                independent_gate: Callable[[ReplayResult], bool]) -> str:
        evidence = tuple(sorted(set(str(x).strip() for x in promotion_evidence if str(x).strip())))
        if not evidence:
            raise ValueError("promotion requires independent evidence")
        if not replay.passed or not independent_gate(replay):
            raise ValueError("independent replay gate did not pass")
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT skill,candidate_skill,replay_digest,status FROM skill_evolution_staging WHERE project=? AND epoch_id=?",
                (self.project, epoch_id),
            ).fetchone()
            if row is None:
                raise KeyError("staged skill epoch not found")
            if row[3] != "staged" or row[2] != replay.digest:
                raise ValueError("promotion receipt does not match staged replay")
            db.execute(
                "UPDATE skill_evolution_staging SET status='promoted' WHERE project=? AND epoch_id=?",
                (self.project, epoch_id),
            )
            db.execute(
                "INSERT OR REPLACE INTO skill_evolution_active VALUES(?,?,?,?,?,?)",
                (self.project, epoch_id, row[1], replay.digest, json.dumps(evidence), _utc()),
            )
        return row[0]

    def active_skill(self) -> str | None:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT skill FROM skill_evolution_active WHERE project=?", (self.project,)).fetchone()
        return row[0] if row else None


class EpisodeSkillEvolution:
    """Bridge EngineeringEpisode -> failure memory -> replay -> staged skill."""

    def __init__(self, memory: PersistentMemory, project: str,
                 *, edit_budget: int = 4) -> None:
        self.memory = memory
        self.project = project.strip()
        self.corpus = EpisodeSkillReplayCorpus(memory, self.project)
        self.optimizer = SkillOptimizer(memory, self.project, edit_budget=edit_budget)

    @staticmethod
    def proposals_from_episode(episode: EngineeringEpisode) -> tuple[SkillEdit, ...]:
        metadata = dict(episode.metadata)
        values: list[SkillEdit] = []
        for key in ("skill_rule", "skill_lesson", "lesson"):
            text = metadata.get(key, "").strip()
            if text:
                values.append(SkillEdit("add", f"- {text}", rationale=f"verified episode {episode.episode_id}"))
        for rule in episode.dont_rules:
            values.append(SkillEdit("add", f"- {rule.strip()}", rationale=f"failure memory {episode.episode_id}"))
        for finding in episode.findings:
            if finding.recommendation.strip():
                values.append(SkillEdit("add", f"- {finding.recommendation.strip()}", rationale=f"review finding {finding.finding_id}"))
        if episode.capability:
            values.append(SkillEdit(
                "add",
                f"- For {dict(episode.metadata).get('task_family', episode.capability)} work, use the verified {episode.capability} capability.",
                rationale=f"episode capability {episode.episode_id}",
            ))
        return tuple(values)

    def ingest_failure_memory(self, episode: EngineeringEpisode) -> tuple[str, ...]:
        if episode.phase != EpisodePhase.FAILED:
            return ()
        ids: list[str] = []
        for rule in episode.dont_rules:
            record = self.memory.remember(
                self.project, "failure-dont", rule,
                intent_digest=episode.intent_digest, confidence=1.0,
                verified=True, approved=True,
            )
            if record:
                ids.append(record.id)
        return tuple(ids)

    def failure_memory_rules(self, *, limit: int = 8) -> tuple[str, ...]:
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(
                "SELECT text FROM memory WHERE project=? AND category='failure-dont' AND verified=1 ORDER BY confidence DESC,created_at DESC LIMIT ?",
                (self.project, limit),
            ).fetchall()
        return tuple(row[0] for row in rows)
    def evolve(
        self,
        *,
        episode: EngineeringEpisode,
        current_skill: str,
        evaluator: Callable[[str, str], SkillScore],
        independent_replay_evaluator: Callable[[str, str], SkillScore],
        task_family: str | None = None,
        holdout_limit: int = 20,
    ) -> SkillEvolutionResult:
        if episode.phase not in {EpisodePhase.COMPLETED, EpisodePhase.FAILED}:
            raise ValueError("only completed or failed episodes can enter skill evolution")
        if not episode.evidence_ids:
            raise ValueError("skill evolution requires episode evidence")
        family = task_family or self.corpus.task_family(episode)
        self.corpus.register_episode(episode, task_family=family)
        failure_memory_ids = self.ingest_failure_memory(episode)
        proposals = list(self.proposals_from_episode(episode))
        existing = {edit.content.strip() for edit in proposals}
        for rule in self.failure_memory_rules():
            content = f"- {rule.strip()}"
            if content not in existing:
                proposals.append(SkillEdit("add", content, rationale="verified failure memory"))
        proposals = tuple(proposals)
        if not proposals:
            raise ValueError("episode produced no bounded skill proposals")
        holdout_ids = self.corpus.holdout(task_family=family, exclude_task_ids=(episode.task_id,), limit=holdout_limit)
        if not holdout_ids:
            raise ValueError("no independent holdout replay cases available")
        train_ids = (episode.task_id,)
        evidence_ids = tuple(sorted(set(episode.evidence_ids) | set(failure_memory_ids)))
        optimization = self.optimizer.epoch(
            task_family=family, skill=current_skill, proposals=proposals,
            train_ids=train_ids, holdout_ids=holdout_ids, evidence_ids=evidence_ids,
            score=lambda skill, ids: self._aggregate_replay(skill, ids, evaluator),
        )
        independent = self.corpus.replay(
            skill=optimization.skill, case_ids=holdout_ids,
            evaluator=independent_replay_evaluator, baseline_skill=current_skill,
        )
        if not optimization.accepted:
            independent = ReplayResult(independent.case_ids, independent.score, False, independent.digest)
        else:
            self.corpus.stage(
                episode=episode, task_family=family, current_skill=current_skill,
                candidate_skill=optimization.skill, holdout_ids=holdout_ids,
                replay=independent, epoch_id=optimization.digest,
            )
        return SkillEvolutionResult(
            episode.episode_id, family, train_ids, holdout_ids, proposals,
            optimization, independent, optimization.accepted and independent.passed, False,
        )

    @staticmethod
    def _aggregate_replay(skill: str, ids: Sequence[str],
                           evaluator: Callable[[str, str], SkillScore]) -> SkillScore:
        if not ids:
            raise ValueError("replay requires case ids")
        scores = [evaluator(case_id, skill) for case_id in sorted(set(ids))]
        if any(not isinstance(s, SkillScore) for s in scores):
            raise TypeError("replay evaluator must return SkillScore")
        return SkillScore(
            sum(x.hard for x in scores) / len(scores),
            sum(x.soft for x in scores) / len(scores),
        )

    def promote(self, result: SkillEvolutionResult, *, promotion_evidence: Iterable[str]) -> str:
        if not result.staged or not result.replay.passed:
            raise ValueError("only independently replay-passed staged results can be promoted")
        return self.corpus.promote(
            epoch_id=result.optimization.digest,
            replay=result.replay,
            promotion_evidence=promotion_evidence,
            independent_gate=lambda replay: replay.passed,
        )

    def planning_skill(self) -> str | None:
        return self.corpus.active_skill()


__all__ = [
    "EpisodeSkillEvolution", "EpisodeSkillReplayCorpus", "ReplayCase",
    "ReplayResult", "SkillEvolutionResult",
]
