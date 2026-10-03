"""Curriculum for filling multi-domain autonomy benchmark gaps.

This planner selects missing benchmark domains/holdout cohorts. It proposes
evaluation work only; it never executes tasks or changes execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .autonomy_benchmark_campaign import AutonomyBenchmarkCampaign


DEFAULT_DOMAINS = (
    "coding",
    "debugging",
    "research",
    "planning",
    "analysis",
    "tool-use",
    "long-horizon",
    "cross-domain",
)


@dataclass(frozen=True)
class CampaignObjective:
    domain: str
    holdout: bool
    priority: float
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "holdout": self.holdout,
            "priority": round(self.priority, 3),
            "rationale": self.rationale,
        }


class AutonomyCampaignCurriculum:
    def __init__(self, *, domains: Iterable[str] = DEFAULT_DOMAINS, minimum_domains: int = 4, minimum_holdout_domains: int = 2, max_objectives: int = 3):
        self.domains = tuple(dict.fromkeys(str(x).strip() for x in domains if str(x).strip()))
        self.minimum_domains = max(2, int(minimum_domains))
        self.minimum_holdout_domains = max(1, int(minimum_holdout_domains))
        self.max_objectives = max(1, int(max_objectives))

    def propose(self, campaign: AutonomyBenchmarkCampaign) -> tuple[CampaignObjective, ...]:
        covered = set(campaign.domains)
        holdouts = set(campaign.holdout_domains)
        rows = []
        for domain in self.domains:
            if domain not in covered:
                rows.append(CampaignObjective(
                    domain, True,
                    1.0 if len(covered) < self.minimum_domains else 0.8,
                    "unseen domain is required for breadth and is proposed as a holdout",
                ))
            elif domain not in holdouts and len(holdouts) < self.minimum_holdout_domains:
                rows.append(CampaignObjective(
                    domain, True, 0.7,
                    "domain is covered but lacks an independent holdout cohort",
                ))
        rows.sort(key=lambda x: (-x.priority, x.domain, not x.holdout))
        return tuple(rows[:self.max_objectives])


__all__ = ["AutonomyCampaignCurriculum", "CampaignObjective", "DEFAULT_DOMAINS"]
