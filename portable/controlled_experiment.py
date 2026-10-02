"""Bounded treatment/control attribution for curriculum experiments.

This layer evaluates externally supplied cohort evidence. It never chooses or
executes an intervention and rejects contaminated cohorts, so causal credit
cannot be inferred from a single improved run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ExperimentObservation:
    experiment_id: str
    observation_id: str
    cohort: str
    metric: float
    evidence_ids: tuple[str, ...]
    holdout: bool = False

    def __post_init__(self) -> None:
        if self.cohort not in {"control", "treatment"}:
            raise ValueError("cohort must be control or treatment")
        if not self.observation_id.strip() or not self.experiment_id.strip():
            raise ValueError("experiment and observation ids are required")
        if not self.evidence_ids:
            raise ValueError("observations require evidence ids")


@dataclass(frozen=True)
class ExperimentAttribution:
    experiment_id: str
    control_mean: float
    treatment_mean: float
    lift: float
    control_samples: int
    treatment_samples: int
    evidence_ids: tuple[str, ...]
    reproducible: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "control_mean": round(self.control_mean, 3),
            "treatment_mean": round(self.treatment_mean, 3),
            "lift": round(self.lift, 3),
            "control_samples": self.control_samples,
            "treatment_samples": self.treatment_samples,
            "evidence_ids": list(self.evidence_ids),
            "reproducible": self.reproducible,
            "reason": self.reason,
        }


class ControlledExperimentAttributor:
    """Compute conservative treatment lift from independent holdout evidence."""

    def __init__(self, *, minimum_samples: int = 2, minimum_lift: float = 0.03) -> None:
        self.minimum_samples = max(1, int(minimum_samples))
        self.minimum_lift = max(0.0, min(1.0, float(minimum_lift)))

    def evaluate(
        self,
        experiment_id: str,
        observations: Iterable[ExperimentObservation],
    ) -> ExperimentAttribution:
        rows = tuple(x for x in observations if x.experiment_id == experiment_id)
        if not rows:
            raise ValueError("no observations for experiment")
        # Only independent holdout observations can establish attribution.
        rows = tuple(x for x in rows if x.holdout)
        if not rows:
            return ExperimentAttribution(
                experiment_id, 0.0, 0.0, 0.0, 0, 0, (), False,
                "no independent holdout observations",
            )
        evidence_sets = [set(x.evidence_ids) for x in rows]
        for idx, left in enumerate(evidence_sets):
            for right in evidence_sets[idx + 1:]:
                if left & right:
                    return ExperimentAttribution(
                        experiment_id, 0.0, 0.0, 0.0, 0, 0, (), False,
                        "evidence overlap prevents independent attribution",
                    )
        control = tuple(x for x in rows if x.cohort == "control")
        treatment = tuple(x for x in rows if x.cohort == "treatment")
        c = sum(max(0.0, min(1.0, x.metric)) for x in control) / len(control) if control else 0.0
        t = sum(max(0.0, min(1.0, x.metric)) for x in treatment) / len(treatment) if treatment else 0.0
        lift = t - c
        reproducible = (
            len(control) >= self.minimum_samples
            and len(treatment) >= self.minimum_samples
            and lift >= self.minimum_lift
        )
        reason = (
            "independent treatment lift reproduced"
            if reproducible
            else "insufficient independent cohorts or treatment lift"
        )
        evidence = tuple(dict.fromkeys(eid for x in rows for eid in x.evidence_ids))
        return ExperimentAttribution(
            experiment_id, c, t, lift, len(control), len(treatment),
            evidence, reproducible, reason,
        )


__all__ = ["ExperimentAttribution", "ExperimentObservation", "ControlledExperimentAttributor"]
