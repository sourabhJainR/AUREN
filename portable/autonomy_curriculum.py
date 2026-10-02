"""Bounded benchmark-driven curriculum selection.

Converts verified autonomy benchmark gaps into explicit learning/evaluation
objectives. It proposes work only; execution authority remains with the runtime.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Sequence
from .autonomy_benchmark import DIMENSIONS


@dataclass(frozen=True)
class CurriculumObjective:
    dimension: str
    objective_id: str
    target: float
    current: float
    gap: float
    priority: float
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "objective_id": self.objective_id,
            "target": round(self.target, 3),
            "current": round(self.current, 3),
            "gap": round(self.gap, 3),
            "priority": round(self.priority, 3),
            "rationale": self.rationale,
        }


class AutonomyCurriculumController:
    """Select the highest-value bounded benchmark gap for the next probe."""

    def __init__(self, *, target: float = 0.75, max_objectives: int = 3) -> None:
        self.target = max(0.0, min(1.0, float(target)))
        self.max_objectives = max(1, int(max_objectives))

    def propose(self, scores: Mapping[str, float]) -> tuple[CurriculumObjective, ...]:
        rows = []
        for dimension in DIMENSIONS:
            current = max(0.0, min(1.0, float(scores.get(dimension, 0.0))))
            gap = max(0.0, self.target - current)
            if gap <= 0:
                continue
            priority = min(1.0, 0.65 * gap + 0.35 * (1.0 - current))
            rows.append(CurriculumObjective(
                dimension,
                f"curriculum:{dimension}",
                self.target,
                current,
                gap,
                priority,
                f"raise verified {dimension} from {current:.2f} toward {self.target:.2f}",
            ))
        rows.sort(key=lambda x: (-x.priority, -x.gap, x.dimension))
        return tuple(rows[:self.max_objectives])


__all__ = ["AutonomyCurriculumController", "CurriculumObjective"]
