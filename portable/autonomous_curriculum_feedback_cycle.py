"""Close the external curriculum loop by turning failures into new holdouts.

This adapter consumes an executed campaign plus its frozen curriculum and
produces discovery signals and a fresh curriculum plan. It never promotes,
mutates capabilities, or owns the external oracle.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json

from .autonomous_curriculum_evolution import AutonomousCurriculumEvolution, CurriculumPlan
from .external_curriculum_campaign_orchestrator import ExternalCurriculumCampaignResult
from .open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery


@dataclass(frozen=True, slots=True)
class CurriculumFeedbackResult:
    prior_campaign_digest: str
    source_evidence_ids: tuple[str, ...]
    discovery_signals: tuple[DiscoverySignal, ...]
    next_curriculum: CurriculumPlan
    feedback_digest: str


class AutonomousCurriculumFeedbackCycle:
    """Convert observed validation failures into a new independent curriculum."""

    def __init__(
        self,
        discovery: OpenEndedTaskEnvironmentDiscovery | None = None,
        evolution: AutonomousCurriculumEvolution | None = None,
        *,
        max_discovery_targets: int = 8,
        max_next_targets: int = 4,
    ) -> None:
        if max_discovery_targets < 1 or max_next_targets < 1:
            raise ValueError("target budgets must be positive")
        self.discovery = discovery or OpenEndedTaskEnvironmentDiscovery()
        self.evolution = evolution or AutonomousCurriculumEvolution()
        self.max_discovery_targets = max_discovery_targets
        self.max_next_targets = max_next_targets

    def derive(
        self,
        prior_curriculum,
        campaign: ExternalCurriculumCampaignResult,
    ) -> CurriculumFeedbackResult:
        if campaign.curriculum_plan_digest != prior_curriculum.plan_digest:
            raise ValueError("campaign does not belong to curriculum plan")
        if not campaign.campaign_digest.strip():
            raise ValueError("campaign digest is required")
        signals = []
        evidence_ids = []
        for result in campaign.target_results:
            if result.validation is None:
                continue
            failed = result.validation.evidence.failed_probe_ids
            for probe_id in failed:
                evidence_ids.append(probe_id)
                signals.append(
                    DiscoverySignal(
                        source_evidence_id=probe_id,
                        failed_domain=result.target_id,
                        failed_constraint="validation-failure",
                        observed_gap=1.0,
                        novel_tool_required=False,
                    )
                )
        discovered = self.discovery.discover(tuple(signals), max_targets=self.max_discovery_targets) if signals else ()
        completed = tuple(
            result.target_id
            for result in campaign.target_results
            if result.successful
        )
        if not discovered:
            raise ValueError("campaign produced no new failure-driven curriculum targets")
        next_plan = self.evolution.build(
            discovered,
            completed_target_ids=completed,
            max_targets=self.max_next_targets,
        )
        payload = {
            "prior_campaign_digest": campaign.campaign_digest,
            "source_evidence_ids": tuple(sorted(set(evidence_ids))),
            "discovery_target_ids": tuple(t.target_id for t in discovered),
            "next_plan_digest": next_plan.plan_digest,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return CurriculumFeedbackResult(
            campaign.campaign_digest, tuple(sorted(set(evidence_ids))), tuple(signals),
            next_plan, digest
        )


__all__ = ["CurriculumFeedbackResult", "AutonomousCurriculumFeedbackCycle"]
