"""Evidence-driven curriculum evolution for unseen evaluation targets.

The curriculum selector chooses what to test next from independent outcomes. It
does not execute tests, invent oracle answers, or promote capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .open_ended_task_environment_discovery import ExplorationTarget


@dataclass(frozen=True, slots=True)
class CurriculumEntry:
    target_id: str
    priority: float
    rationale: tuple[str, ...]
    prerequisite_target_ids: tuple[str, ...]
    holdout_required: bool
    independent_oracle_required: bool


@dataclass(frozen=True, slots=True)
class CurriculumPlan:
    plan_id: str
    entries: tuple[CurriculumEntry, ...]
    max_targets: int
    plan_digest: str

    @property
    def trustworthy(self) -> bool:
        return (
            bool(self.entries)
            and all(e.holdout_required and e.independent_oracle_required for e in self.entries)
        )


class AutonomousCurriculumEvolution:
    """Select harder and novel targets while preserving evaluation independence."""

    def build(
        self,
        targets: tuple[ExplorationTarget, ...],
        *,
        completed_target_ids: tuple[str, ...] = (),
        max_targets: int = 4,
    ) -> CurriculumPlan:
        if max_targets < 1:
            raise ValueError("max_targets must be positive")
        completed = set(completed_target_ids)
        candidates = [t for t in targets if t.target_id not in completed and t.trustworthy]
        candidates.sort(key=lambda t: (-t.novelty_score, t.target_id))
        selected = candidates[:max_targets]
        entries = tuple(
            CurriculumEntry(
                target_id=t.target_id,
                priority=t.novelty_score,
                rationale=(
                    "unseen evaluation target",
                    f"environment-dimension:{t.environment_dimension}",
                    "fresh-holdout",
                ),
                prerequisite_target_ids=(),
                holdout_required=True,
                independent_oracle_required=True,
            )
            for t in selected
        )
        payload = {
            "entries": [
                {
                    "target_id": e.target_id,
                    "priority": e.priority,
                    "rationale": e.rationale,
                    "prerequisite_target_ids": e.prerequisite_target_ids,
                }
                for e in entries
            ],
            "max_targets": max_targets,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CurriculumPlan(
            "curriculum-" + digest[:16], entries, max_targets, digest
        )


__all__=["CurriculumEntry","CurriculumPlan","AutonomousCurriculumEvolution"]
