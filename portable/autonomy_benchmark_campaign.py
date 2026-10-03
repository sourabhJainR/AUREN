"""Multi-domain, holdout-based autonomy benchmark campaign.

A single episode is insufficient evidence of general capability. This campaign
requires independent episodes across multiple task domains and explicit
holdouts before aggregating the existing six-dimensional autonomy gate.
Provider-free and evaluation-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .autonomy_benchmark import AutonomyBenchmark, AutonomyBenchmarkGate, DIMENSIONS


@dataclass(frozen=True)
class CampaignEpisode:
    episode_id: str
    domain: str
    holdout: bool
    scores: Mapping[str, float]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.episode_id.strip() or not self.domain.strip():
            raise ValueError("episode id and domain are required")
        if not self.evidence_ids:
            raise ValueError("campaign episodes require evidence ids")
        if any(not 0.0 <= float(v) <= 1.0 for v in self.scores.values()):
            raise ValueError("campaign scores must be within [0,1]")


@dataclass(frozen=True)
class AutonomyBenchmarkCampaign:
    episodes: tuple[CampaignEpisode, ...]
    benchmark: AutonomyBenchmark
    domains: tuple[str, ...]
    holdout_domains: tuple[str, ...]
    independent: bool
    breadth_passed: bool
    gate_passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_count": len(self.episodes),
            "domains": list(self.domains),
            "holdout_domains": list(self.holdout_domains),
            "independent": self.independent,
            "breadth_passed": self.breadth_passed,
            "benchmark": self.benchmark.as_dict(),
            "gate_passed": self.gate_passed,
        }


class AutonomyBenchmarkCampaignRunner:
    def __init__(self, *, minimum_domains: int = 4, minimum_holdout_domains: int = 2):
        self.minimum_domains = max(2, int(minimum_domains))
        self.minimum_holdout_domains = max(1, int(minimum_holdout_domains))

    def evaluate(self, episodes: Iterable[CampaignEpisode]) -> AutonomyBenchmarkCampaign:
        rows = tuple(episodes)
        if not rows:
            raise ValueError("benchmark campaign requires episodes")
        ids = [row.episode_id for row in rows]
        evidence = [eid for row in rows for eid in row.evidence_ids]
        independent = len(ids) == len(set(ids)) and len(evidence) == len(set(evidence))
        domains = tuple(sorted({row.domain for row in rows}))
        holdout_domains = tuple(sorted({row.domain for row in rows if row.holdout}))
        breadth_passed = (
            independent
            and len(domains) >= self.minimum_domains
            and len(holdout_domains) >= self.minimum_holdout_domains
        )
        scores = {}
        for dimension in DIMENSIONS:
            values = [max(0.0, min(1.0, float(row.scores.get(dimension, 0.0)))) for row in rows]
            scores[dimension] = sum(values) / len(values)
        benchmark = AutonomyBenchmarkGate().evaluate(scores)
        gate_passed = breadth_passed and benchmark.gate_passed
        return AutonomyBenchmarkCampaign(
            rows, benchmark, domains, holdout_domains, independent, breadth_passed, gate_passed
        )


__all__ = ["AutonomyBenchmarkCampaign", "AutonomyBenchmarkCampaignRunner", "CampaignEpisode"]
