"""Persistent, bounded queue for curriculum-controlled experiments.

The queue carries experiment contracts across independent execution episodes.
It is a measurement/control-plane mechanism only: it cannot execute an
intervention or promote a capability. Experiments remain pending until enough
independent control/treatment observations exist for downstream attribution.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from runtime.task_memory import approach_history
from .curriculum_experiment import CurriculumExperiment
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class ExperimentQueueState:
    experiment: CurriculumExperiment
    control_samples: int
    treatment_samples: int
    completed: bool
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment": self.experiment.as_dict(),
            "control_samples": self.control_samples,
            "treatment_samples": self.treatment_samples,
            "completed": self.completed,
            "status": self.status,
        }


class AutonomousExperimentQueue:
    PREFIX = "team:experiment-queue:"
    ORCHESTRATION_PREFIX = "team:experiment-orchestration:"

    def __init__(self, root, *, minimum_samples_per_cohort: int = 2):
        self.root = Path(root)
        self.minimum_samples_per_cohort = max(1, int(minimum_samples_per_cohort))

    def enqueue(self, experiment: CurriculumExperiment) -> None:
        key = self.PREFIX + experiment.experiment_id
        existing = approach_history(self.root, key, limit=1, exact=True)
        if existing:
            return
        LearningSteward(self.root, run_id="experiment-queue", task=experiment.objective_id).record_experience(
            key=key,
            outcome="partial",
            evidence_quality=experiment.baseline_metric,
            cost_score=experiment.resource_budget,
            duration_seconds=0.0,
            decision=json.dumps({"status": "queued", "experiment": experiment.as_dict()}, sort_keys=True),
        )

    def _states(self) -> tuple[ExperimentQueueState, ...]:
        rows = approach_history(self.root, self.PREFIX, limit=200, exact=False)
        latest: dict[str, dict] = {}
        for row in rows:
            key = str(row.get("approach", ""))
            if not key.startswith(self.PREFIX):
                continue
            latest[key] = row
        states = []
        for key, row in latest.items():
            try:
                detail = json.loads(str(row.get("detail", "{}")))
                decision = detail.get("decision", {})
                if isinstance(decision, str):
                    decision = json.loads(decision)
                exp_data = decision["experiment"]
                experiment = CurriculumExperiment(**{
                    name: exp_data[name]
                    for name in CurriculumExperiment.__dataclass_fields__
                    if name in exp_data
                })
                status = str(decision.get("status", "queued"))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            control, treatment = self._observation_counts(experiment.experiment_id)
            states.append(ExperimentQueueState(
                experiment,
                control,
                treatment,
                control >= self.minimum_samples_per_cohort and treatment >= self.minimum_samples_per_cohort,
                status,
            ))
        states.sort(key=lambda x: x.experiment.experiment_id)
        return tuple(states)

    def _observation_counts(self, experiment_id: str) -> tuple[int, int]:
        # Queue revisions are the canonical lifecycle ledger. Reading them
        # directly avoids a second key family that can drift from queue state.
        rows = approach_history(
            self.root, self.PREFIX + experiment_id, limit=100, exact=True
        )
        counts = {"control": set(), "treatment": set()}
        for row in rows:
            try:
                detail = json.loads(str(row.get("detail", "{}")))
                decision = detail.get("decision", {})
                if isinstance(decision, str):
                    decision = json.loads(decision)
                if decision.get("status") != "observed":
                    continue
                cohort = str(decision.get("cohort", ""))
                episode = str(decision.get("episode_id", ""))
                if cohort in counts and episode:
                    counts[cohort].add(episode)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return len(counts["control"]), len(counts["treatment"])

    def next(self) -> ExperimentQueueState | None:
        candidates = [state for state in self._states() if not state.completed]
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x.experiment.target_metric + x.experiment.baseline_metric, x.experiment.experiment_id))
        return candidates[0]

    def mark_assigned(self, experiment_id: str, episode_id: str, cohort: str) -> None:
        state = next((x for x in self._states() if x.experiment.experiment_id == experiment_id), None)
        if state is None:
            return
        key = self.PREFIX + experiment_id
        LearningSteward(self.root, run_id=episode_id, task=experiment_id).record_experience(
            key=key,
            outcome="partial",
            evidence_quality=state.experiment.baseline_metric,
            cost_score=state.experiment.resource_budget,
            duration_seconds=0.0,
            decision=json.dumps({"status": "assigned", "experiment": state.experiment.as_dict(), "episode_id": episode_id, "cohort": cohort}, sort_keys=True),
        )

    def mark_observed(self, experiment_id: str, observation: dict[str, object]) -> None:
        state = next((x for x in self._states() if x.experiment.experiment_id == experiment_id), None)
        if state is None:
            return
        key = self.PREFIX + experiment_id
        LearningSteward(self.root, run_id=str(observation.get("episode_id", "episode")), task=experiment_id).record_experience(
            key=key,
            outcome="worked" if bool(observation.get("attributable")) else "partial",
            evidence_quality=float(observation.get("metric", state.experiment.baseline_metric)),
            cost_score=state.experiment.resource_budget,
            duration_seconds=0.0,
            decision=json.dumps({"status": "observed", "experiment": state.experiment.as_dict(), **observation}, sort_keys=True),
            evidence_ids=observation.get("evidence_ids", ()),
        )


__all__ = ["AutonomousExperimentQueue", "ExperimentQueueState"]
