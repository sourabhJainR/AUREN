"""Bind resource-aware curriculum schedules to external campaign execution."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from .autonomous_curriculum_evolution import CurriculumPlan, CurriculumEntry
from .external_curriculum_campaign_orchestrator import ExternalCurriculumCampaignOrchestrator, ExternalCurriculumCampaignResult
from .resource_aware_curriculum_scheduler import CurriculumSchedule, ResourceAwareCurriculumScheduler

@dataclass(frozen=True, slots=True)
class ScheduledCampaignResult:
    schedule_digest: str
    campaign: ExternalCurriculumCampaignResult
    scheduled_target_ids: tuple[str, ...]
    resource_budget_respected: bool
    execution_digest: str

class ScheduledCurriculumCampaignExecutor:
    """Execute exactly the targets selected by a validated resource schedule."""

    def __init__(self, scheduler: ResourceAwareCurriculumScheduler,
                 orchestrator: ExternalCurriculumCampaignOrchestrator) -> None:
        self.scheduler, self.orchestrator = scheduler, orchestrator

    @staticmethod
    def _subset(curriculum: CurriculumPlan, ids: tuple[str, ...]) -> CurriculumPlan:
        selected = tuple(e for e in curriculum.entries if e.target_id in ids)
        if len(selected) != len(ids):
            raise ValueError("schedule contains an unknown curriculum target")
        payload = {
            "entries": [{"target_id": e.target_id, "priority": e.priority,
                         "rationale": e.rationale,
                         "prerequisite_target_ids": e.prerequisite_target_ids}
                        for e in selected],
            "max_targets": len(selected),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return CurriculumPlan("scheduled-" + digest[:16], selected, len(selected), digest)

    def run(self, curriculum: CurriculumPlan, *,
            schedule: CurriculumSchedule | None = None,
            max_parallel: int = 2, duration_budget: float = 3600.0,
            memory_budget_mb: int = 4096) -> ScheduledCampaignResult:
        schedule = schedule or self.scheduler.build(
            curriculum, max_parallel=max_parallel,
            duration_budget=duration_budget, memory_budget_mb=memory_budget_mb
        )
        if schedule.curriculum_plan_digest != curriculum.plan_digest:
            raise ValueError("schedule does not belong to curriculum plan")
        if not schedule.targets:
            raise ValueError("schedule contains no targets")
        ids = tuple(x.target_id for x in schedule.targets)
        subset = self._subset(curriculum, ids)
        campaign = self.orchestrator.run(subset, expected_plan_digest=subset.plan_digest,
                                         max_steps=None)
        # The scheduler is the authoritative pre-execution budget check. The
        # orchestrator result is still untrusted if any target failed.
        respected = (
            sum(x.estimated_duration for x in schedule.targets) <= schedule.duration_budget
            and max(x.estimated_memory_mb for x in schedule.targets) <= schedule.memory_budget_mb
        )
        payload = {"schedule_digest": schedule.schedule_digest,
                   "campaign_digest": campaign.campaign_digest,
                   "target_ids": ids, "resource_budget_respected": respected}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ScheduledCampaignResult(schedule.schedule_digest, campaign, ids, respected, digest)

__all__=["ScheduledCampaignResult","ScheduledCurriculumCampaignExecutor"]
