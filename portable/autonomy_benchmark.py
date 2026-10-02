"""Objective autonomy benchmark gates for the evolving engineering runtime.

This is an engineering capability gate, not a claim of general intelligence.
It measures completion, transfer, calibration, causal learning, safe autonomy,
and efficiency from externally supplied verified episode metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


DIMENSIONS = (
    "goal_completion", "cross_task_transfer", "self_model_calibration",
    "causal_learning", "safe_autonomy", "resource_efficiency",
)


@dataclass(frozen=True)
class AutonomyBenchmark:
    scores: Mapping[str, float]
    overall: float
    passed_dimensions: tuple[str, ...]
    failed_dimensions: tuple[str, ...]
    gate_passed: bool
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "scores": {k: round(float(v), 3) for k, v in self.scores.items()},
            "overall": round(self.overall, 3),
            "passed_dimensions": list(self.passed_dimensions),
            "failed_dimensions": list(self.failed_dimensions),
            "gate_passed": self.gate_passed,
            "rationale": self.rationale,
        }


class AutonomyBenchmarkGate:
    """Deterministic, dependency-free acceptance gate over verified metrics."""

    def __init__(self, *, dimension_threshold: float = .75, overall_threshold: float = .82) -> None:
        self.dimension_threshold = max(0.0, min(1.0, float(dimension_threshold)))
        self.overall_threshold = max(0.0, min(1.0, float(overall_threshold)))

    def evaluate(self, metrics: Mapping[str, float]) -> AutonomyBenchmark:
        missing = tuple(name for name in DIMENSIONS if name not in metrics)
        if missing:
            raise ValueError("missing benchmark dimensions: " + ", ".join(missing))
        scores = {name: max(0.0, min(1.0, float(metrics[name]))) for name in DIMENSIONS}
        passed = tuple(name for name in DIMENSIONS if scores[name] >= self.dimension_threshold)
        failed = tuple(name for name in DIMENSIONS if name not in passed)
        overall = sum(scores.values()) / len(DIMENSIONS)
        gate = not failed and overall >= self.overall_threshold
        rationale = (
            "all autonomy dimensions meet the benchmark gate"
            if gate else
            "benchmark gate not met; continue capability improvement and re-measure on verified evidence"
        )
        return AutonomyBenchmark(scores, overall, passed, failed, gate, rationale)


__all__ = ["AutonomyBenchmark", "AutonomyBenchmarkGate", "DIMENSIONS"]
