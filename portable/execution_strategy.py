"""Shared bounded execution strategy and pathway selection.

This module is the single policy contract between adaptive learning and the
graph runtime. Learned choices can influence execution, but deterministic
safety limits remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .counterfactual_engine import BranchCandidate, CounterfactualEngine
from .experience_router import ExperienceRouter


@dataclass(frozen=True)
class ExecutionStrategy:
    name: str
    known: bool
    attempt_multiplier: float
    verification_depth: str
    resource_lane: str
    capability_bias: tuple[str, ...]
    minimum_evidence: int


_PROFILES = {
    "default": ExecutionStrategy("default", True, 1.0, "standard", "auto", (), 1),
    "evidence-first": ExecutionStrategy("evidence-first", True, 1.0, "deep", "auto", ("verifier", "structured_output"), 2),
    "deep-verify": ExecutionStrategy("deep-verify", True, 1.25, "independent", "agent", ("verifier", "structured_output"), 2),
    "fast-path": ExecutionStrategy("fast-path", True, 0.75, "standard", "auto", ("agent",), 1),
}


def execution_strategy(name: str | None) -> ExecutionStrategy:
    key = str(name or "default").strip().lower()
    profile = _PROFILES.get(key)
    return profile if profile is not None else ExecutionStrategy("default", False, 1.0, "standard", "auto", (), 1)


_DEPTH = {"standard": 1, "deep": 2, "independent": 3, "human": 4}


def max_verification_depth(*depths: str) -> str:
    values = tuple(str(value) for value in depths if value is not None)
    return max(values or ("standard",), key=lambda value: _DEPTH.get(value, 1))


@dataclass(frozen=True)
class ExecutionPathway:
    capability: str
    resource_lane: str
    verification_depth: str
    retry_action: str
    score: float
    confidence: float
    rationale: str


class PathwayOptimizer:
    """Discover bounded execution pathways from history plus learned policy.

    It explores alternatives instead of hard-coding one route, then chooses
    through the same counterfactual engine used elsewhere in HWS.
    """

    def __init__(self, experience: ExperienceRouter) -> None:
        self.experience = experience

    def discover(
        self,
        *,
        capabilities: Sequence[str],
        key_prefix: str,
        strategy: ExecutionStrategy,
        risk: float,
        evidence_quality: float,
        resource_lanes: Sequence[str] = ("agent", "local"),
    ) -> ExecutionPathway:
        if not capabilities:
            raise ValueError("at least one capability is required")
        candidates: list[BranchCandidate] = []
        rows: list[ExecutionPathway] = []
        depth = max_verification_depth(strategy.verification_depth, "standard")
        preferred = set(strategy.capability_bias)
        for capability in capabilities:
            summary = self.experience.summarize(f"{key_prefix}:capability:{capability}")
            success = summary.success_rate if summary else 0.5
            evidence = summary.evidence_quality if summary else evidence_quality
            cost = summary.avg_cost if summary else 0.5
            failure = summary.failure_rate if summary else risk
            bias = 0.08 if capability in preferred else 0.0
            for lane in resource_lanes:
                lane_penalty = 0.08 if strategy.resource_lane not in {"auto", lane} else 0.0
                score = max(0.01, 0.55 * success + 0.25 * evidence + 0.15 * (1.0 - cost) + bias - lane_penalty)
                pathway = ExecutionPathway(
                    capability, lane, depth,
                    "retry" if failure >= 0.55 else "stop",
                    score, summary.confidence if summary else 0.25,
                    "historical evidence + learned strategy + bounded lane exploration",
                )
                rows.append(pathway)
                candidates.append(BranchCandidate(
                    f"{capability}:{lane}", success, evidence, cost + lane_penalty,
                    min(1.0, failure + risk * 0.25),
                    summary.confidence if summary else 0.25,
                    min(1.0, summary.avg_latency / 300.0) if summary else 0.5,
                    rationale=pathway.rationale,
                ))
        engine = CounterfactualEngine(min_confidence=0.45, min_margin=0.02)
        decision = engine.evaluate({"key": key_prefix, "risk": risk, "strategy": strategy.name}, candidates)
        if not decision.abstained:
            selected = next((item for item in rows if f"{item.capability}:{item.resource_lane}" == decision.selected), None)
            if selected is not None:
                return selected
        return max(rows, key=lambda item: (item.score, item.confidence, item.capability, item.resource_lane))


__all__ = ["ExecutionPathway", "ExecutionStrategy", "PathwayOptimizer", "execution_strategy", "max_verification_depth"]
