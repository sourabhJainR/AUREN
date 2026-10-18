"""Versioned, deterministic engineering plans and fail-closed lifecycle transitions.

This module owns the plan contract only. Execution, budgets, cancellation and durable
checkpoints remain owned by AdaptiveRuntime and its canonical RuntimeExecutionGuard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


PLAN_SCHEMA_VERSION = "auren-engineering-plan-v1"
RUN_SCHEMA_VERSION = "auren-engineering-plan-run-v1"
TASK_STATUSES = frozenset({
    "pending", "ready", "running", "verifying", "passed",
    "blocked", "failed", "cancelled",
})
RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"ready", "blocked", "cancelled"}),
    "ready": frozenset({"running", "blocked", "cancelled"}),
    "running": frozenset({"verifying", "blocked", "failed", "cancelled"}),
    "verifying": frozenset({"passed", "blocked", "failed", "cancelled"}),
    "blocked": frozenset({"ready", "cancelled"}),
    "failed": frozenset({"ready", "cancelled"}),
    "passed": frozenset(),
    "cancelled": frozenset(),
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("plan data must be finite JSON-compatible values") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _nonempty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class PlanTask:
    task_id: str
    title: str
    acceptance_criteria: tuple[str, ...]
    rollback_plan: str
    depends_on: tuple[str, ...] = ()
    risk: str = "medium"

    def __post_init__(self) -> None:
        _nonempty(self.task_id, "task_id")
        _nonempty(self.title, "title")
        _nonempty(self.rollback_plan, "rollback_plan")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"risk must be one of {sorted(RISK_LEVELS)}")
        if not self.acceptance_criteria or any(not isinstance(item, str) or not item.strip() for item in self.acceptance_criteria):
            raise ValueError("each task requires non-empty, verifiable acceptance criteria")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"task {self.task_id} has duplicate dependencies")
        if self.task_id in self.depends_on:
            raise ValueError(f"task {self.task_id} cannot depend on itself")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "title": self.title,
            "acceptance_criteria": list(self.acceptance_criteria),
            "rollback_plan": self.rollback_plan, "depends_on": list(self.depends_on),
            "risk": self.risk,
        }


@dataclass(frozen=True)
class EngineeringPlan:
    run_id: str
    goal: str
    requirements: tuple[str, ...]
    tasks: tuple[PlanTask, ...]
    schema_version: str = PLAN_SCHEMA_VERSION
    source_revision: str = ""
    policy_digest: str = ""
    budget: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        _nonempty(self.goal, "goal")
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported plan schema: {self.schema_version}")
        _nonempty(self.source_revision, "source_revision")
        if not self.requirements or any(not isinstance(item, str) or not item.strip() for item in self.requirements):
            raise ValueError("plan requires explicit non-empty requirements")
        if not self.tasks:
            raise ValueError("plan must contain at least one task")
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task IDs must be unique")
        known = set(ids)
        for task in self.tasks:
            missing = set(task.depends_on) - known
            if missing:
                raise ValueError(f"task {task.task_id} has unknown dependencies: {sorted(missing)}")
        # A cycle makes a plan impossible to schedule, so reject it at construction.
        dependencies = {task.task_id: set(task.depends_on) for task in self.tasks}
        visited: set[str] = set()
        active: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in active:
                raise ValueError(f"dependency cycle includes task {task_id}")
            if task_id in visited:
                return
            active.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            active.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)
        if self.budget:
            for name, amount in self.budget.items():
                if not isinstance(name, str) or not name.strip() or type(amount) is not int or amount < 1:
                    raise ValueError("budget entries require named positive integer limits")
        object.__setattr__(self, "budget", MappingProxyType(dict(sorted(self.budget.items()))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "run_id": self.run_id,
            "goal": self.goal, "requirements": list(self.requirements),
            "tasks": [task.to_dict() for task in self.tasks],
            "source_revision": self.source_revision, "policy_digest": self.policy_digest,
            "budget": dict(sorted(self.budget.items())),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def ready_tasks(self, completed_task_ids: set[str] | frozenset[str]) -> tuple[str, ...]:
        known = {task.task_id for task in self.tasks}
        if not set(completed_task_ids) <= known:
            raise ValueError("completed task set contains IDs outside this plan")
        return tuple(
            task.task_id for task in self.tasks
            if task.task_id not in completed_task_ids
            and set(task.depends_on) <= set(completed_task_ids)
        )


@dataclass(frozen=True)
class TaskProgress:
    task_id: str
    status: str = "pending"
    evidence_refs: tuple[str, ...] = ()
    replan_generation: int = 0
    transition_reason: str = "created"

    def __post_init__(self) -> None:
        _nonempty(self.task_id, "task_id")
        if self.status not in TASK_STATUSES:
            raise ValueError(f"unknown task status: {self.status}")
        if type(self.replan_generation) is not int or self.replan_generation < 0:
            raise ValueError("replan_generation must be a non-negative integer")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence references must be non-empty strings")
        _nonempty(self.transition_reason, "transition_reason")

    def transition(
        self, target: str, *, reason: str, evidence_refs: tuple[str, ...] = (),
        replan_generation: int | None = None,
    ) -> "TaskProgress":
        if target not in _ALLOWED_TRANSITIONS.get(self.status, frozenset()):
            raise ValueError(f"illegal task transition: {self.status} -> {target}")
        reason = _nonempty(reason, "reason")
        next_generation = self.replan_generation if replan_generation is None else replan_generation
        if self.status in {"failed", "blocked"} and target == "ready":
            if next_generation <= self.replan_generation:
                raise ValueError("replanning must advance replan_generation")
        elif next_generation != self.replan_generation:
            raise ValueError("replan_generation may change only when reopening failed or blocked work")
        combined_evidence = tuple(dict.fromkeys((*self.evidence_refs, *evidence_refs)))
        if target == "passed" and not combined_evidence:
            raise ValueError("a task cannot pass without evidence references")
        return TaskProgress(
            task_id=self.task_id, status=target, evidence_refs=combined_evidence,
            replan_generation=next_generation, transition_reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "replan_generation": self.replan_generation,
            "transition_reason": self.transition_reason,
        }


@dataclass(frozen=True)
class EngineeringPlanRun:
    plan_digest: str
    source_revision: str
    tasks: tuple[TaskProgress, ...]
    schema_version: str = RUN_SCHEMA_VERSION
    revision: int = 0
    handoff: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != RUN_SCHEMA_VERSION:
            raise ValueError(f"unsupported run schema: {self.schema_version}")
        if len(self.plan_digest) != 64 or any(ch not in "0123456789abcdef" for ch in self.plan_digest.lower()):
            raise ValueError("plan_digest must be a SHA-256 hex digest")
        _nonempty(self.source_revision, "source_revision")
        if not isinstance(self.handoff, Mapping):
            raise ValueError("handoff must be a JSON-compatible mapping")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("run task IDs must be unique")
        _canonical(dict(self.handoff))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "plan_digest": self.plan_digest,
            "source_revision": self.source_revision, "revision": self.revision,
            "tasks": [task.to_dict() for task in self.tasks],
            "handoff": dict(self.handoff),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def transition_task(
        self, task_id: str, target: str, *, plan: EngineeringPlan, reason: str,
        evidence_refs: tuple[str, ...] = (), replan_generation: int | None = None,
    ) -> "EngineeringPlanRun":
        self.verify_against(plan)
        progress_by_id = {item.task_id: item for item in self.tasks}
        task_definition = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task_definition is None:
            raise KeyError(f"task {task_id!r} is not present in this plan")
        if target in {"ready", "running"}:
            incomplete = [dependency for dependency in task_definition.depends_on
                          if progress_by_id[dependency].status != "passed"]
            if incomplete:
                raise ValueError(f"task {task_id} has incomplete dependencies: {incomplete}")
        found = False
        updated: list[TaskProgress] = []
        for progress in self.tasks:
            if progress.task_id == task_id:
                found = True
                updated.append(progress.transition(
                    target, reason=reason, evidence_refs=evidence_refs,
                    replan_generation=replan_generation,
                ))
            else:
                updated.append(progress)
        if not found:
            raise KeyError(f"task {task_id!r} is not present in this run")
        return EngineeringPlanRun(
            plan_digest=self.plan_digest, source_revision=self.source_revision,
            tasks=tuple(updated), schema_version=self.schema_version,
            revision=self.revision + 1, handoff=self.handoff,
        )

    def verify_against(self, plan: EngineeringPlan) -> None:
        if self.plan_digest != plan.digest:
            raise ValueError("run plan digest mismatch; refusing incompatible resume")
        if {item.task_id for item in self.tasks} != {item.task_id for item in plan.tasks}:
            raise ValueError("run task set does not match plan")
        if self.source_revision and plan.source_revision and self.source_revision != plan.source_revision:
            raise ValueError("source revision mismatch; refusing incompatible resume")


def new_plan_run(plan: EngineeringPlan) -> EngineeringPlanRun:
    return EngineeringPlanRun(
        plan_digest=plan.digest, source_revision=plan.source_revision,
        tasks=tuple(TaskProgress(task_id=task.task_id) for task in plan.tasks),
    )
