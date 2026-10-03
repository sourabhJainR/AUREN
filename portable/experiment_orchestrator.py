"""Bounded orchestration for curriculum-controlled execution experiments.

The orchestrator binds a planned curriculum experiment to an execution episode
and assigns a deterministic control/treatment cohort. It does not execute
interventions, bypass runtime authority, or declare causal success; downstream
evidence attribution and benchmark/capability gates remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from .curriculum_experiment import CurriculumExperiment
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class ExperimentAssignment:
    experiment_id: str
    episode_id: str
    cohort: str
    intervention_allowed: bool
    risk_budget: float
    resource_budget: float
    holdout_required: bool
    assignment_digest: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "episode_id": self.episode_id,
            "cohort": self.cohort,
            "intervention_allowed": self.intervention_allowed,
            "risk_budget": round(self.risk_budget, 3),
            "resource_budget": round(self.resource_budget, 3),
            "holdout_required": self.holdout_required,
            "assignment_digest": self.assignment_digest,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExperimentObservationRecord:
    experiment_id: str
    episode_id: str
    cohort: str
    metric: float
    evidence_ids: tuple[str, ...]
    holdout: bool
    attributable: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "episode_id": self.episode_id,
            "cohort": self.cohort,
            "metric": round(self.metric, 3),
            "evidence_ids": list(self.evidence_ids),
            "holdout": self.holdout,
            "attributable": self.attributable,
            "reason": self.reason,
        }


class ClosedLoopExperimentOrchestrator:
    """Bind, observe and persist experiment episodes without execution authority."""

    def __init__(self, root, *, max_risk: float = 0.25, max_resource: float = 0.50):
        self.root = root
        self.max_risk = max(0.0, min(1.0, float(max_risk)))
        self.max_resource = max(0.0, min(1.0, float(max_resource)))

    @staticmethod
    def _digest(experiment_id: str, episode_id: str, cohort: str) -> str:
        raw = f"{experiment_id}:{episode_id}:{cohort}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def assign(
        self,
        experiment: CurriculumExperiment,
        *,
        episode_id: str,
        preferred_cohort: str | None = None,
    ) -> ExperimentAssignment:
        if not episode_id.strip():
            raise ValueError("episode id is required")
        cohort = preferred_cohort
        if cohort is None:
            digest = hashlib.sha256(
                f"{experiment.experiment_id}:{episode_id}".encode()
            ).digest()
            cohort = "treatment" if digest[0] % 2 else "control"
        if cohort not in {"control", "treatment"}:
            raise ValueError("cohort must be control or treatment")
        risk = min(self.max_risk, max(0.0, float(experiment.risk_budget)))
        resource = min(self.max_resource, max(0.0, float(experiment.resource_budget)))
        intervention_allowed = cohort == "treatment" and risk > 0.0 and resource > 0.0
        reason = (
            "treatment is eligible for the bounded experiment intervention"
            if intervention_allowed
            else "control cohort receives baseline execution with no experiment intervention"
        )
        return ExperimentAssignment(
            experiment.experiment_id,
            episode_id,
            cohort,
            intervention_allowed,
            risk,
            resource,
            bool(experiment.holdout_required),
            self._digest(experiment.experiment_id, episode_id, cohort),
            reason,
        )

    def observe(
        self,
        assignment: ExperimentAssignment,
        *,
        metric: float,
        evidence_ids: Iterable[str],
        holdout: bool,
    ) -> ExperimentObservationRecord:
        ids = tuple(dict.fromkeys(str(x).strip() for x in evidence_ids if str(x).strip()))
        if not ids:
            raise ValueError("experiment observation requires evidence ids")
        if assignment.holdout_required and not holdout:
            return ExperimentObservationRecord(
                assignment.experiment_id, assignment.episode_id, assignment.cohort,
                max(0.0, min(1.0, float(metric))), ids, False, False,
                "holdout evidence is required before attribution",
            )
        if len(ids) != len(set(ids)):
            return ExperimentObservationRecord(
                assignment.experiment_id, assignment.episode_id, assignment.cohort,
                max(0.0, min(1.0, float(metric))), ids, bool(holdout), False,
                "duplicate evidence ids prevent independent attribution",
            )
        result = ExperimentObservationRecord(
            assignment.experiment_id, assignment.episode_id, assignment.cohort,
            max(0.0, min(1.0, float(metric))), ids, bool(holdout), True,
            "episode observation is eligible for downstream independent attribution",
        )
        LearningSteward(
            self.root, run_id=assignment.episode_id, task=assignment.experiment_id
        ).record_experience(
            key=f"team:experiment-orchestration:{assignment.experiment_id}:{assignment.cohort}",
            outcome="passed" if result.attributable else "failed",
            evidence_quality=result.metric,
            cost_score=assignment.resource_budget,
            duration_seconds=0.0,
            decision=str(result.as_dict()),
            evidence_ids=ids,
        )
        return result

    def control_and_treatment_ready(
        self,
        assignments: Iterable[ExperimentAssignment],
    ) -> bool:
        rows = tuple(assignments)
        cohorts = {row.cohort for row in rows}
        digests = {row.assignment_digest for row in rows}
        return {"control", "treatment"} <= cohorts and len(digests) == len(rows)


__all__ = [
    "ClosedLoopExperimentOrchestrator",
    "ExperimentAssignment",
    "ExperimentObservationRecord",
]
