"""Deterministic active-information selection for AUREN reasoning loops.

The planner chooses what to inspect or test next using expected uncertainty
reduction per unit cost. It proposes work only; existing capability, security
and orchestration layers decide whether and how the action can execute.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InformationAction:
    action_id: str
    description: str
    expected_gain: float
    cost: float = 1.0
    risk: float = 0.0
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.action_id.strip() or not self.description.strip():
            raise ValueError("action_id and description are required")
        if not 0 <= self.expected_gain <= 1 or self.cost <= 0 or not 0 <= self.risk <= 1:
            raise ValueError("expected_gain and risk must be between 0 and 1, and cost positive")
        if any(not item.strip() for item in self.evidence_ids):
            raise ValueError("evidence_ids must contain non-empty strings")


@dataclass(frozen=True)
class InformationPlan:
    action_id: str
    score: float
    uncertainty_before: float
    expected_uncertainty_after: float
    reason: str


class InformationPlanner:
    """Select the highest-value bounded information action deterministically."""

    def choose(self, *, uncertainty: float, actions: tuple[InformationAction, ...],
               max_risk: float = 1.0) -> InformationPlan | None:
        if not 0 <= uncertainty <= 1:
            raise ValueError("uncertainty must be between 0 and 1")
        if not 0 <= max_risk <= 1:
            raise ValueError("max_risk must be between 0 and 1")
        eligible = [action for action in actions if action.risk <= max_risk and action.expected_gain > 0]
        if not eligible:
            return None
        ranked = sorted(eligible, key=lambda action: (-self._score(action), action.action_id))
        selected = ranked[0]
        gain = min(uncertainty, selected.expected_gain)
        after = max(0.0, uncertainty - gain)
        return InformationPlan(selected.action_id, round(self._score(selected), 6), uncertainty,
                               round(after, 6), "maximizes expected uncertainty reduction per cost and risk")

    @staticmethod
    def _score(action: InformationAction) -> float:
        return action.expected_gain * (1.0 - action.risk) / action.cost


__all__ = ["InformationAction", "InformationPlan", "InformationPlanner"]
