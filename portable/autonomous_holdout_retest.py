"""Generate bounded independent holdout retests from campaign learning.

This module only plans benchmark contracts. It cannot execute, promote, or
change runtime authority. Retests must be on a different holdout identity and
prefer a different domain when the campaign contains a known failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .autonomous_campaign_learning import CampaignIntervention, CampaignLearning
from .benchmark_task_contract import BenchmarkTaskContract, BenchmarkTaskContractFactory


@dataclass(frozen=True)
class HoldoutRetestPlan:
    campaign_id: str
    source_task_id: str
    intervention: CampaignIntervention
    contract: BenchmarkTaskContract
    reason: str


class AutonomousHoldoutRetestPlanner:
    def __init__(self, *, max_retests: int = 8) -> None:
        self.max_retests = max(1, min(8, int(max_retests)))

    def plan(
        self,
        learning: CampaignLearning,
        *,
        available_domains: Iterable[str],
        existing_task_ids: Iterable[str] = (),
        source_domains: Mapping[str, str] | None = None,
    ) -> tuple[HoldoutRetestPlan, ...]:
        domains = tuple(sorted({str(x).strip() for x in available_domains if str(x).strip()}))
        if not domains:
            return ()
        existing = set(existing_task_ids) | set(learning.selected_task_ids)
        source_domains = dict(source_domains or {})
        selected_domains = set()
        plans: list[HoldoutRetestPlan] = []

        for intervention in learning.interventions:
            if len(plans) >= self.max_retests:
                break
            # Prefer a domain not exercised by the source campaign. This makes
            # the retest a transfer check rather than a replay of the failure.
            source_domain = source_domains.get(intervention.task_id)
            candidates = [d for d in domains if d != source_domain and d not in selected_domains]
            if not candidates:
                candidates = [d for d in domains if d not in selected_domains]
            if not candidates:
                continue
            domain = candidates[0]
            rationale = (
                f"independent holdout retest for {intervention.failure_class} "
                f"intervention from {intervention.task_id}; {intervention.action}"
            )
            contract = BenchmarkTaskContractFactory().create(
                domain=domain, holdout=True, rationale=rationale
            )
            if contract.task_id in existing:
                continue
            selected_domains.add(domain)
            existing.add(contract.task_id)
            plans.append(
                HoldoutRetestPlan(
                    learning.campaign_id,
                    intervention.task_id,
                    intervention,
                    contract,
                    "independent holdout required before capability policy changes",
                )
            )
        return tuple(plans)


__all__ = ["AutonomousHoldoutRetestPlanner", "HoldoutRetestPlan"]
