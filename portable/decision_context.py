"""Deterministic workload context for adaptive execution decisions.

The profile is deliberately provider-free and bounded. It converts available
graph/resource signals into normalized features so the decision fabric can
adapt without requiring a new model, plugin, or service.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DecisionContext:
    complexity: float
    dependency_parallelism: float
    resource_pressure: float
    latency_pressure: float
    evidence_value: float
    failure_risk: float

    def __post_init__(self) -> None:
        for name in ("complexity", "dependency_parallelism", "resource_pressure",
                     "latency_pressure", "evidence_value", "failure_risk"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    @property
    def fingerprint(self) -> str:
        payload = "|".join(
            f"{getattr(self, name):.2f}"
            for name in ("complexity", "dependency_parallelism",
                         "resource_pressure", "latency_pressure",
                         "evidence_value", "failure_risk")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "complexity": round(self.complexity, 3),
            "dependency_parallelism": round(self.dependency_parallelism, 3),
            "resource_pressure": round(self.resource_pressure, 3),
            "latency_pressure": round(self.latency_pressure, 3),
            "evidence_value": round(self.evidence_value, 3),
            "failure_risk": round(self.failure_risk, 3),
            "fingerprint": self.fingerprint,
        }


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def build_decision_context(
    *,
    task: str,
    agents: Sequence[Any],
    pressure: Mapping[str, Any],
    timeout_seconds: float,
) -> DecisionContext:
    """Build a stable workload profile from signals already owned by the runtime."""
    count = len(agents)
    edges = sum(len(getattr(agent, "depends_on", ()) or ()) for agent in agents)
    read_only = sum(bool(getattr(agent, "read_only", True)) for agent in agents)
    critical = sum(bool(getattr(agent, "critical", True)) for agent in agents)
    max_parallel = max(1, max((len(level) for level in _levels(agents)), default=1))
    complexity = _clip(
        0.20 * min(1.0, count / 12.0)
        + 0.20 * min(1.0, edges / max(1, count * 2))
        + 0.20 * min(1.0, max_parallel / 4.0)
        + 0.15 * (1.0 - read_only / max(1, count))
        + 0.15 * (critical / max(1, count))
        + 0.10 * min(1.0, len(str(task)) / 4000.0)
    )
    dependency_parallelism = _clip(max_parallel / max(1, min(4, count)))
    resource_pressure = _clip(
        0.40 * float(pressure.get("cpu_pressure", 0.0))
        + 0.30 * float(pressure.get("queue_pressure", 0.0))
        + 0.30 * float(pressure.get("memory_pressure", 0.0))
    )
    estimated = sum(float(getattr(agent, "estimated_duration_seconds", 30.0) or 30.0)
                    for agent in agents) / max(1, count)
    latency_pressure = _clip(estimated / max(1.0, float(timeout_seconds)))
    evidence_value = _clip(
        sum(float(getattr(agent, "evidence_value", 0.7) or 0.7) for agent in agents)
        / max(1, count)
    )
    failure_risk = _clip(
        0.55 * (critical / max(1, count))
        + 0.25 * complexity
        + 0.20 * resource_pressure
    )
    return DecisionContext(
        complexity=complexity,
        dependency_parallelism=dependency_parallelism,
        resource_pressure=resource_pressure,
        latency_pressure=latency_pressure,
        evidence_value=evidence_value,
        failure_risk=failure_risk,
    )


def _levels(agents: Sequence[Any]) -> list[list[Any]]:
    remaining = {getattr(a, "name"): a for a in agents}
    done: set[str] = set()
    levels: list[list[Any]] = []
    while remaining:
        ready = [a for a in remaining.values()
                 if set(getattr(a, "depends_on", ()) or ()).issubset(done)]
        if not ready:
            break
        levels.append(ready)
        for agent in ready:
            done.add(getattr(agent, "name"))
            remaining.pop(getattr(agent, "name"), None)
    return levels


def context_adjustment(strategy: str, mode: str, context: DecisionContext) -> float:
    """Return a small bounded preference delta; evidence remains dominant."""
    delta = 0.0
    if mode == "parallel":
        delta += 0.04 * context.dependency_parallelism
        delta -= 0.05 * context.resource_pressure
    elif mode == "serial":
        delta += 0.04 * context.failure_risk
        delta -= 0.025 * context.dependency_parallelism
    elif mode == "verify-heavy":
        delta += 0.04 * (context.failure_risk * context.evidence_value)
        delta -= 0.025 * context.latency_pressure
    elif mode == "balanced":
        delta += 0.015 * (1.0 - abs(context.resource_pressure - 0.5) * 2.0)

    if strategy == "fast-path":
        delta += 0.04 * context.latency_pressure * (1.0 - context.failure_risk)
        delta -= 0.04 * context.evidence_value * context.failure_risk
    elif strategy == "deep-verify":
        delta += 0.04 * context.failure_risk * context.evidence_value
    elif strategy == "evidence-first":
        delta += 0.025 * context.evidence_value
    return max(-0.08, min(0.08, delta))


__all__ = ["DecisionContext", "build_decision_context", "context_adjustment"]
