"""Amdahl-aware delivery analysis for dependency-constrained engineering plans.

This is a planning projection, not an executor or competing scheduler. It estimates
what can run concurrently, identifies the dependency critical path, accounts for
coordination cost, and returns a deterministic bounded schedule for the canonical
ExecutionController to execute if policy and workspace admission permit.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from portable.engineering_plan import EngineeringPlan


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    worker_slot: int
    start_seconds: float
    finish_seconds: float
    duration_seconds: float
    resource_keys: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryAcceleration:
    sequential_seconds: float
    critical_path_seconds: float
    scheduled_seconds: float
    coordination_seconds: float
    worker_limit: int
    serial_fraction: float
    amdahl_upper_bound: float
    scheduled_speedup: float
    critical_path_speedup_bound: float
    tasks: tuple[ScheduledTask, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "sequential_seconds": self.sequential_seconds,
            "critical_path_seconds": self.critical_path_seconds,
            "scheduled_seconds": self.scheduled_seconds,
            "coordination_seconds": self.coordination_seconds,
            "worker_limit": self.worker_limit,
            "serial_fraction": self.serial_fraction,
            "amdahl_upper_bound": self.amdahl_upper_bound,
            "scheduled_speedup": self.scheduled_speedup,
            "critical_path_speedup_bound": self.critical_path_speedup_bound,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "worker_slot": task.worker_slot,
                    "start_seconds": task.start_seconds,
                    "finish_seconds": task.finish_seconds,
                    "duration_seconds": task.duration_seconds,
                    "resource_keys": list(task.resource_keys),
                }
                for task in self.tasks
            ],
        }


def analyze_delivery_parallelism(
    plan: EngineeringPlan,
    *,
    durations_seconds: Mapping[str, float],
    worker_limit: int,
    resource_locks: Mapping[str, Sequence[str]] | None = None,
    coordination_overhead_seconds: float = 0.0,
    serial_task_ids: Sequence[str] = (),
) -> DeliveryAcceleration:
    """Build a deterministic, resource-aware schedule and Amdahl bound."""
    if type(worker_limit) is not int or worker_limit < 1 or worker_limit > 256:
        raise ValueError("worker_limit must be between 1 and 256")
    if (
        isinstance(coordination_overhead_seconds, bool)
        or not isinstance(coordination_overhead_seconds, (int, float))
        or not math.isfinite(coordination_overhead_seconds)
        or coordination_overhead_seconds < 0
    ):
        raise ValueError("coordination overhead must be a finite non-negative number")

    task_ids = [task.task_id for task in plan.tasks]
    if set(durations_seconds) != set(task_ids):
        missing = sorted(set(task_ids) - set(durations_seconds))
        extra = sorted(set(durations_seconds) - set(task_ids))
        raise ValueError(f"duration map must exactly match plan tasks (missing={missing}, extra={extra})")

    durations: dict[str, float] = {}
    for task_id, value in durations_seconds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"duration for {task_id} must be finite and positive")
        durations[task_id] = float(value)

    locks_by_task: dict[str, tuple[str, ...]] = {}
    resource_locks = resource_locks or {}
    unknown_lock_tasks = set(resource_locks) - set(task_ids)
    if unknown_lock_tasks:
        raise ValueError(f"resource locks reference unknown tasks: {sorted(unknown_lock_tasks)}")
    for task_id in task_ids:
        raw = resource_locks.get(task_id, ())
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ValueError(f"resource locks for {task_id} must be a sequence of keys")
        if any(not isinstance(key, str) or not key.strip() for key in raw):
            raise ValueError(f"resource lock keys for {task_id} must be non-empty strings")
        keys = tuple(sorted(set(raw)))
        locks_by_task[task_id] = keys

    definitions = {task.task_id: task for task in plan.tasks}
    plan_order = {task_id: index for index, task_id in enumerate(task_ids)}
    remaining = {task_id: set(definitions[task_id].depends_on) for task_id in task_ids}
    topological: list[str] = []
    while remaining:
        ready = sorted(
            (task_id for task_id, dependencies in remaining.items() if not dependencies),
            key=plan_order.__getitem__,
        )
        if not ready:
            raise ValueError("plan dependency graph is cyclic")
        for task_id in ready:
            topological.append(task_id)
            remaining.pop(task_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)

    worker_free = [0.0] * worker_limit
    resource_free: dict[str, float] = {}
    finish: dict[str, float] = {}
    scheduled: list[ScheduledTask] = []
    critical_finish: dict[str, float] = {}
    critical_path = 0.0

    for task_id in topological:
        task = definitions[task_id]
        dependency_finish = max((finish[item] for item in task.depends_on), default=0.0)
        locks = locks_by_task[task_id]
        resource_ready = max((resource_free.get(key, 0.0) for key in locks), default=0.0)
        worker_slot = min(
            range(worker_limit),
            key=lambda slot: (max(dependency_finish, worker_free[slot], resource_ready), slot),
        )
        start = max(dependency_finish, worker_free[worker_slot], resource_ready)
        coordination = float(coordination_overhead_seconds) if task.depends_on else 0.0
        effective_duration = durations[task_id] + coordination
        end = start + effective_duration
        finish[task_id] = end
        worker_free[worker_slot] = end
        for key in locks:
            resource_free[key] = end
        critical_finish[task_id] = max(
            (critical_finish[item] for item in task.depends_on), default=0.0
        ) + durations[task_id]
        critical_path = max(critical_path, critical_finish[task_id])
        scheduled.append(ScheduledTask(
            task_id=task_id, worker_slot=worker_slot, start_seconds=start,
            finish_seconds=end, duration_seconds=durations[task_id], resource_keys=locks,
        ))

    if isinstance(serial_task_ids, (str, bytes)):
        raise ValueError("serial_task_ids must be a sequence of task IDs")
    if any(not isinstance(task_id, str) or not task_id for task_id in serial_task_ids):
        raise ValueError("serial_task_ids must contain non-empty task IDs")
    serial_ids = set(serial_task_ids)
    if not serial_ids <= set(task_ids):
        raise ValueError(f"serial_task_ids contain unknown tasks: {sorted(serial_ids - set(task_ids))}")

    sequential = sum(durations.values())
    coordination_total = float(coordination_overhead_seconds) * sum(
        1 for task in plan.tasks if task.depends_on
    )
    scheduled_total = max(finish.values(), default=0.0)
    serial_fraction = sum(durations[task_id] for task_id in serial_ids) / sequential if sequential else 1.0
    amdahl_bound = 1.0 / (serial_fraction + (1.0 - serial_fraction) / worker_limit)
    scheduled_speedup = sequential / scheduled_total if scheduled_total else 1.0
    critical_path_bound = min(float(worker_limit), sequential / critical_path) if critical_path else 1.0

    return DeliveryAcceleration(
        sequential_seconds=sequential, critical_path_seconds=critical_path,
        scheduled_seconds=scheduled_total, coordination_seconds=coordination_total,
        worker_limit=worker_limit, serial_fraction=serial_fraction,
        amdahl_upper_bound=amdahl_bound, scheduled_speedup=scheduled_speedup,
        critical_path_speedup_bound=critical_path_bound,
        tasks=tuple(sorted(scheduled, key=lambda item: (item.start_seconds, item.worker_slot, item.task_id))),
    )


__all__ = ["DeliveryAcceleration", "ScheduledTask", "analyze_delivery_parallelism"]
