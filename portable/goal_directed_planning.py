"""Goal-directed cognitive state for AUREN.

Provides a small persistent goal decomposition primitive so planning can be
conditioned on desired outcomes, blockers, and evidence gaps rather than only
the immediate task string. It is intentionally provider-free.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

@dataclass(frozen=True)
class Goal:
    goal_id: str
    description: str
    priority: float = 0.5
    status: str = "open"
    parent_id: str | None = None
    depends_on: tuple[str, ...] = ()
    evidence_required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal_id.strip() or not self.description.strip():
            raise ValueError("goal id and description are required")
        if self.status not in {"open","active","blocked","complete","abandoned"}:
            raise ValueError("invalid goal status")
        if not 0.0 <= float(self.priority) <= 1.0:
            raise ValueError("priority must be between 0 and 1")

    def as_dict(self) -> dict[str, object]:
        return {"goal_id":self.goal_id,"description":self.description,
                "priority":round(self.priority,3),"status":self.status,
                "parent_id":self.parent_id,"depends_on":list(self.depends_on),
                "evidence_required":list(self.evidence_required)}

class GoalDirectedPlanner:
    """Select the next executable goal while preserving dependency order."""

    def next_goal(self, goals: Iterable[Goal]) -> Goal | None:
        items=tuple(goals)
        by_id={g.goal_id:g for g in items}
        ready=[g for g in items if g.status in {"open","active"}
               and all(by_id.get(dep) is not None and by_id[dep].status=="complete"
                       for dep in g.depends_on)]
        if not ready:
            return None
        return max(ready,key=lambda g:(g.priority,g.status=="active",-len(g.depends_on),g.goal_id))

    def blocked(self, goals: Iterable[Goal]) -> tuple[Goal,...]:
        items=tuple(goals); by_id={g.goal_id:g for g in items}
        return tuple(g for g in items if g.status in {"open","active"}
                     and any(by_id.get(dep) is None or by_id[dep].status not in {"complete"}
                             for dep in g.depends_on))

__all__=["Goal","GoalDirectedPlanner"]
