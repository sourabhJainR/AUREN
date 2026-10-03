"""Resource-aware scheduling for autonomous curriculum campaigns.

Scheduling is policy-only. It does not execute targets or grant execution
authority. Historical resource observations influence lane selection while
explicit budgets bound the resulting schedule.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib, json
from .autonomous_curriculum_evolution import CurriculumPlan
from .resource_calibration import ResourceCalibrator

@dataclass(frozen=True, slots=True)
class ScheduledTarget:
    target_id: str
    task_class: str
    lane: str
    estimated_duration: float
    estimated_memory_mb: int
    priority: float

@dataclass(frozen=True, slots=True)
class CurriculumSchedule:
    curriculum_plan_digest: str
    targets: tuple[ScheduledTarget, ...]
    max_parallel: int
    duration_budget: float
    memory_budget_mb: int
    schedule_digest: str

class ResourceAwareCurriculumScheduler:
    """Create a bounded execution plan from a frozen curriculum."""

    def __init__(self, calibrator: ResourceCalibrator | None = None) -> None:
        self.calibrator = calibrator or ResourceCalibrator()

    def build(self, curriculum: CurriculumPlan, *, max_parallel: int = 2,
              duration_budget: float = 3600.0, memory_budget_mb: int = 4096) -> CurriculumSchedule:
        if not curriculum.trustworthy:
            raise ValueError("curriculum plan is not trustworthy")
        if max_parallel < 1 or duration_budget <= 0 or memory_budget_mb < 1:
            raise ValueError("invalid scheduling budget")
        rows = []
        for entry in curriculum.entries:
            task_class = entry.rationale[1].split(":", 1)[-1] if len(entry.rationale) > 1 else "general"
            lane = self.calibrator.route(task_class)
            rows.append(ScheduledTarget(entry.target_id, task_class, lane,
                                        300.0 if lane == "local" else 600.0,
                                        512 if lane == "local" else 1024, entry.priority))
        rows.sort(key=lambda x: (-x.priority, x.estimated_duration, x.target_id))
        if sum(x.estimated_duration for x in rows) > duration_budget:
            raise ValueError("curriculum exceeds duration budget")
        if rows and max(x.estimated_memory_mb for x in rows) > memory_budget_mb:
            raise ValueError("curriculum exceeds memory budget")
        payload = {"curriculum_plan_digest": curriculum.plan_digest,
                   "targets": [asdict(x) for x in rows], "max_parallel": max_parallel,
                   "duration_budget": duration_budget, "memory_budget_mb": memory_budget_mb}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return CurriculumSchedule(curriculum.plan_digest, tuple(rows), max_parallel,
                                  duration_budget, memory_budget_mb, digest)

__all__ = ["ScheduledTarget", "CurriculumSchedule", "ResourceAwareCurriculumScheduler"]
