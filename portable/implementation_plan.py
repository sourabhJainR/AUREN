"""Dependency-safe implementation planning bound to requirement revisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Any


@dataclass(frozen=True)
class WorkStep:
    step_id: str
    description: str
    depends_on: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImplementationPlan:
    steps: tuple[WorkStep, ...]
    parallel_groups: tuple[tuple[str, ...], ...]
    requirement_contract_digest: str = ""
    requirement_contract_revision: int = 0
    stale_step_ids: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        ids = {step.step_id for step in self.steps}
        return (
            bool(self.steps)
            and not self.stale_step_ids
            and all(
                set(step.depends_on) <= ids
                and bool(step.verification)
                for step in self.steps
            )
        )


class ImplementationPlanner:
    def build(self, steps: Sequence[WorkStep], *, requirement_contract: Any | None = None) -> ImplementationPlan:
        items = tuple(steps)
        if any(not isinstance(step, WorkStep) for step in items):
            raise TypeError("steps must contain WorkStep records")
        ids = [step.step_id.strip() for step in items]
        if not items or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("steps need unique IDs")
        known = set(ids)
        for step in items:
            if step.step_id in step.depends_on or not set(step.depends_on) <= known:
                raise ValueError("invalid dependency graph")
            if not step.verification:
                raise ValueError(f"{step.step_id}: verification required")

        digest = ""
        revision = 0
        if requirement_contract is not None:
            if not isinstance(getattr(requirement_contract, "digest", None), str):
                raise TypeError("requirement contract must expose a digest")
            requirement_ids = {
                item.requirement_id for item in getattr(requirement_contract, "requirements", ())
            }
            for step in items:
                if not step.requirement_ids:
                    raise ValueError(f"{step.step_id}: requirement mapping is required for a bound plan")
                if not set(step.requirement_ids) <= requirement_ids:
                    raise ValueError(f"{step.step_id}: references an unknown requirement")
            digest = requirement_contract.digest
            revision = requirement_contract.revision

        groups = []
        done = set()
        remaining = {step.step_id: step for step in items}
        while remaining:
            ready = tuple(sorted(key for key, step in remaining.items() if set(step.depends_on) <= done))
            if not ready:
                raise ValueError("dependency graph contains a cycle")
            groups.append(ready)
            done.update(ready)
            for key in ready:
                remaining.pop(key)
        normalized = tuple(WorkStep(
            step.step_id, step.description, tuple(step.depends_on),
            tuple(step.verification), tuple(step.requirement_ids),
        ) for step in items)
        return ImplementationPlan(normalized, tuple(groups), digest, revision)

    def invalidate_for_requirement_change(
        self,
        plan: ImplementationPlan,
        requirement_contract: Any,
        changed_requirement_ids: Sequence[str],
    ) -> ImplementationPlan:
        if not isinstance(plan, ImplementationPlan) or not plan.requirement_contract_digest:
            raise ValueError("only a requirement-bound plan can be invalidated")
        if not isinstance(getattr(requirement_contract, "digest", None), str):
            raise TypeError("requirement contract must expose a digest")
        changed = set(changed_requirement_ids)
        known = {item.requirement_id for item in getattr(requirement_contract, "requirements", ())}
        if not changed or not changed <= known:
            raise ValueError("changed requirement IDs must be non-empty and present in the revised contract")

        stale = set(plan.stale_step_ids)
        stale.update(
            step.step_id for step in plan.steps
            if changed.intersection(step.requirement_ids)
        )
        changed_in_pass = True
        while changed_in_pass:
            changed_in_pass = False
            for step in plan.steps:
                if step.step_id not in stale and stale.intersection(step.depends_on):
                    stale.add(step.step_id)
                    changed_in_pass = True
        return ImplementationPlan(
            plan.steps, plan.parallel_groups,
            requirement_contract.digest, requirement_contract.revision,
            tuple(sorted(stale)),
        )

    @staticmethod
    def validate_binding(plan: ImplementationPlan, requirement_contract: Any) -> None:
        if not isinstance(plan, ImplementationPlan):
            raise TypeError("plan must be an ImplementationPlan")
        if (
            plan.requirement_contract_digest != getattr(requirement_contract, "digest", None)
            or plan.requirement_contract_revision != getattr(requirement_contract, "revision", None)
        ):
            raise ValueError("implementation plan is stale for the current requirement contract")
        if plan.stale_step_ids:
            raise ValueError(
                "implementation plan contains invalidated steps: " + ", ".join(plan.stale_step_ids)
            )


__all__ = ["WorkStep", "ImplementationPlan", "ImplementationPlanner"]
