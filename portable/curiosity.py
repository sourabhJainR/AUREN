"""Deterministic learning-frontier selection for AUREN."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _bounded(value: float, field: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not 0 <= numeric <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return numeric


@dataclass(frozen=True)
class LearningNeed:
    capability: str
    uncertainty: float
    importance: float
    expected_gain: float
    cost: float
    risk: float

    def __post_init__(self) -> None:
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise ValueError("capability must be non-empty")
        object.__setattr__(self, "capability", self.capability.strip())
        object.__setattr__(self, "uncertainty", _bounded(self.uncertainty, "uncertainty"))
        object.__setattr__(self, "importance", _bounded(self.importance, "importance"))
        object.__setattr__(self, "expected_gain", _bounded(self.expected_gain, "expected_gain"))
        object.__setattr__(self, "risk", _bounded(self.risk, "risk"))
        try:
            cost = float(self.cost)
        except (TypeError, ValueError) as exc:
            raise ValueError("cost must be numeric") from exc
        if cost <= 0:
            raise ValueError("cost must be positive")
        object.__setattr__(self, "cost", cost)


@dataclass(frozen=True)
class LearningChoice:
    capability: str
    score: float
    uncertainty: float
    importance: float
    expected_gain: float
    cost: float
    risk: float


class CuriosityEngine:
    """Rank unresolved learning needs without executing any external action."""

    def score(self, need: LearningNeed) -> float:
        value = need.uncertainty * need.importance * need.expected_gain
        return (value / need.cost) * (1.0 - need.risk)

    def rank(self, needs: Iterable[LearningNeed]) -> list[LearningChoice]:
        choices = [LearningChoice(
            capability=need.capability,
            score=self.score(need),
            uncertainty=need.uncertainty,
            importance=need.importance,
            expected_gain=need.expected_gain,
            cost=need.cost,
            risk=need.risk,
        ) for need in needs]
        return sorted(choices, key=lambda choice: (-choice.score, choice.capability))

    def choose(self, needs: Iterable[LearningNeed], *, max_cost: float, max_risk: float) -> LearningChoice | None:
        if max_cost <= 0:
            raise ValueError("max_cost must be positive")
        max_risk = _bounded(max_risk, "max_risk")
        eligible = [need for need in needs if need.cost <= max_cost and need.risk <= max_risk]
        ranked = self.rank(eligible)
        return ranked[0] if ranked else None


__all__ = ["CuriosityEngine", "LearningChoice", "LearningNeed"]
