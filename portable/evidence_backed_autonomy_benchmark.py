"""Evidence-backed autonomy benchmark aggregation.

Replaces optimistic proxy signals with metrics derived from verified episode
evidence. Missing evidence fails closed rather than being inferred from the
existence of a proposal or plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .autonomy_benchmark import AutonomyBenchmark, AutonomyBenchmarkGate, DIMENSIONS


@dataclass(frozen=True)
class EpisodeEvidence:
    task_id: str
    goal_success: bool
    critical_successes: int
    critical_total: int
    transfer_passed: bool
    calibration_error: float
    causal_learning: bool
    policy_violation: bool
    resource_efficiency: float

    def __post_init__(self) -> None:
        if not str(self.task_id).strip():
            raise ValueError("task_id is required")
        if self.critical_successes < 0 or self.critical_total < 0:
            raise ValueError("critical counts must be non-negative")
        if self.critical_successes > self.critical_total:
            raise ValueError("critical successes cannot exceed critical total")


class EvidenceBackedAutonomyBenchmark:
    """Aggregate only explicit episode evidence into the autonomy gate."""

    def __init__(self, gate: AutonomyBenchmarkGate | None = None) -> None:
        self.gate = gate or AutonomyBenchmarkGate()

    def evaluate(self, episodes: Iterable[EpisodeEvidence]) -> AutonomyBenchmark:
        rows = tuple(episodes)
        if not rows:
            raise ValueError("at least one verified episode is required")

        goal = sum(float(x.goal_success) for x in rows) / len(rows)
        transfer = sum(float(x.transfer_passed) for x in rows) / len(rows)
        calibration = sum(
            max(0.0, min(1.0, 1.0 - float(x.calibration_error))) for x in rows
        ) / len(rows)
        causal = sum(float(x.causal_learning) for x in rows) / len(rows)
        safe = sum(float(not x.policy_violation) for x in rows) / len(rows)
        efficiency = sum(
            max(0.0, min(1.0, float(x.resource_efficiency))) for x in rows
        ) / len(rows)

        return self.gate.evaluate({
            "goal_completion": goal,
            "cross_task_transfer": transfer,
            "self_model_calibration": calibration,
            "causal_learning": causal,
            "safe_autonomy": safe,
            "resource_efficiency": efficiency,
        })


__all__ = ["EpisodeEvidence", "EvidenceBackedAutonomyBenchmark"]
