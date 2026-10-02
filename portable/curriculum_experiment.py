"""Bounded curriculum-to-experiment control loop.

Turns a benchmark gap into a reproducible experiment contract and closes it
only when externally observed benchmark/evidence data is supplied. Planning is
provider-free and advisory; execution authority remains with the runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

from .autonomy_curriculum import CurriculumObjective
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class CurriculumExperiment:
    experiment_id: str
    objective_id: str
    dimension: str
    hypothesis: str
    baseline_metric: float
    target_metric: float
    intervention: str
    success_delta: float
    risk_budget: float
    resource_budget: float
    holdout_required: bool
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "objective_id": self.objective_id,
            "dimension": self.dimension,
            "hypothesis": self.hypothesis,
            "baseline_metric": round(self.baseline_metric, 3),
            "target_metric": round(self.target_metric, 3),
            "intervention": self.intervention,
            "success_delta": round(self.success_delta, 3),
            "risk_budget": round(self.risk_budget, 3),
            "resource_budget": round(self.resource_budget, 3),
            "holdout_required": self.holdout_required,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CurriculumExperimentOutcome:
    experiment_id: str
    objective_id: str
    before: float
    after: float
    delta: float
    evidence_ids: tuple[str, ...]
    success: bool
    learning_key: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "objective_id": self.objective_id,
            "before": round(self.before, 3),
            "after": round(self.after, 3),
            "delta": round(self.delta, 3),
            "evidence_ids": list(self.evidence_ids),
            "success": self.success,
            "learning_key": self.learning_key,
        }


class CurriculumExperimentController:
    """Create and close bounded experiments for curriculum objectives."""

    def __init__(
        self,
        root: Path,
        *,
        minimum_success_delta: float = 0.03,
        risk_budget: float = 0.25,
        resource_budget: float = 0.50,
    ) -> None:
        self.root = Path(root)
        self.minimum_success_delta = max(0.0, min(1.0, float(minimum_success_delta)))
        self.risk_budget = max(0.0, min(1.0, float(risk_budget)))
        self.resource_budget = max(0.0, min(1.0, float(resource_budget)))

    @staticmethod
    def _experiment_id(objective: CurriculumObjective) -> str:
        raw = json.dumps(
            {
                "objective_id": objective.objective_id,
                "dimension": objective.dimension,
                "target": round(objective.target, 6),
                "current": round(objective.current, 6),
            },
            sort_keys=True,
        )
        return "curriculum-experiment:" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _intervention(dimension: str) -> str:
        return f"run a controlled probe that specifically measures {dimension} without changing unrelated execution policy"

    def plan(
        self,
        objective: CurriculumObjective,
        *,
        risk_budget: float | None = None,
        resource_budget: float | None = None,
    ) -> CurriculumExperiment:
        baseline = max(0.0, min(1.0, float(objective.current)))
        target = max(baseline, min(1.0, float(objective.target)))
        success_delta = min(
            max(0.0, target - baseline),
            self.minimum_success_delta if target - baseline >= self.minimum_success_delta else target - baseline,
        )
        if target > baseline and success_delta <= 0:
            raise ValueError("curriculum objective has no measurable improvement target")
        return CurriculumExperiment(
            experiment_id=self._experiment_id(objective),
            objective_id=objective.objective_id,
            dimension=objective.dimension,
            hypothesis=(
                f"targeted intervention for {objective.dimension} should improve the "
                f"verified benchmark from {baseline:.3f} toward {target:.3f}"
            ),
            baseline_metric=baseline,
            target_metric=target,
            intervention=self._intervention(objective.dimension),
            success_delta=success_delta,
            risk_budget=self.risk_budget if risk_budget is None else max(0.0, min(1.0, float(risk_budget))),
            resource_budget=self.resource_budget if resource_budget is None else max(0.0, min(1.0, float(resource_budget))),
            holdout_required=True,
            rationale="benchmark gap is converted into a measurable, isolated, evidence-backed probe",
        )

    def close(
        self,
        experiment: CurriculumExperiment,
        *,
        benchmark_after: float,
        evidence_ids: Iterable[str],
        role: str = "team",
    ) -> CurriculumExperimentOutcome:
        ids = tuple(dict.fromkeys(str(x).strip() for x in evidence_ids if str(x).strip()))
        if not ids:
            raise ValueError("controlled experiment requires evidence ids")
        after = max(0.0, min(1.0, float(benchmark_after)))
        before = experiment.baseline_metric
        delta = after - before
        success = delta >= experiment.success_delta and after >= before
        key = f"{role}:curriculum-experiment:{experiment.experiment_id}"
        LearningSteward(
            self.root, run_id="curriculum-experiment", task=experiment.objective_id
        ).record_experience(
            key=key,
            outcome="passed" if success else "failed",
            evidence_quality=after,
            cost_score=experiment.resource_budget,
            duration_seconds=0.0,
            decision=json.dumps(
                {
                    "experiment": experiment.as_dict(),
                    "before": before,
                    "after": after,
                    "delta": delta,
                    "success": success,
                },
                sort_keys=True,
            ),
            evidence_ids=ids,
        )
        return CurriculumExperimentOutcome(
            experiment.experiment_id,
            experiment.objective_id,
            before,
            after,
            delta,
            ids,
            success,
            key,
        )


__all__ = ["CurriculumExperiment", "CurriculumExperimentController", "CurriculumExperimentOutcome"]
