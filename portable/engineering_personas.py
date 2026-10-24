"""Independent planning, implementation and review contracts for AUREN.

Callbacks are supplied by the existing runtime/provider adapters. This module
defines role separation and evidence contracts; it does not create a second
scheduler or grant execution/promotion authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AgentPersona:
    role: str
    agent_id: str
    persona: str
    model_id: str = ""

    def __post_init__(self) -> None:
        if not self.role.strip() or not self.agent_id.strip() or not self.persona.strip():
            raise ValueError("agent role, identity and persona are required")


@dataclass(frozen=True)
class PersonaRoster:
    planner: AgentPersona
    plan_reviewer: AgentPersona
    implementer: AgentPersona
    implementation_reviewer: AgentPersona

    def validate(self) -> None:
        roles = {
            "planner": self.planner,
            "plan_reviewer": self.plan_reviewer,
            "implementer": self.implementer,
            "implementation_reviewer": self.implementation_reviewer,
        }
        for expected_role, identity in roles.items():
            if identity.role != expected_role:
                raise ValueError(f"{expected_role} requires role={expected_role!r}")
        ids = [identity.agent_id for identity in roles.values()]
        if len(set(ids)) != len(ids):
            raise ValueError("planner, implementer and reviewers must have distinct agent identities")
        if self.planner.persona == self.implementer.persona:
            raise ValueError("planning and implementation must use different personas")
        if self.plan_reviewer.persona in {self.planner.persona, self.implementer.persona}:
            raise ValueError("plan reviewer must use an independent persona")
        if self.implementation_reviewer.persona in {self.planner.persona, self.implementer.persona}:
            raise ValueError("implementation reviewer must use an independent persona")
        if self.plan_reviewer.persona == self.implementation_reviewer.persona:
            raise ValueError("plan and post-implementation review must use different personas")


@dataclass(frozen=True)
class EngineeringPlan:
    goal: str
    scope: tuple[str, ...]
    assumptions: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...]
    alternatives_considered: tuple[str, ...]
    risks_and_mitigations: tuple[str, ...]
    test_strategy: tuple[str, ...]
    rollback_plan: tuple[str, ...]
    budget: Mapping[str, Any] = field(default_factory=dict)
    source_revision: str = ""
    plan_digest: str = ""

    def validate(self) -> None:
        required = {
            "goal": self.goal,
            "scope": self.scope,
            "acceptance_criteria": self.acceptance_criteria,
            "alternatives_considered": self.alternatives_considered,
            "risks_and_mitigations": self.risks_and_mitigations,
            "test_strategy": self.test_strategy,
            "rollback_plan": self.rollback_plan,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("plan is incomplete: " + ", ".join(missing))
        if any(not str(item).strip() for values in (
            self.scope, self.acceptance_criteria, self.alternatives_considered,
            self.risks_and_mitigations, self.test_strategy, self.rollback_plan,
        ) for item in values):
            raise ValueError("plan fields must not contain empty entries")


@dataclass(frozen=True)
class PersonaReview:
    reviewer_id: str
    target_id: str
    approved: bool
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    dimensions: Mapping[str, float] = field(default_factory=dict)

    def validate(self, *, reviewer: AgentPersona, target: AgentPersona) -> None:
        if self.reviewer_id != reviewer.agent_id:
            raise ValueError("review identity does not match the configured reviewer")
        if self.target_id != target.agent_id:
            raise ValueError("review target does not match the reviewed agent")
        if self.reviewer_id == self.target_id:
            raise ValueError("self-review is forbidden")
        if any(not str(key).strip() for key in self.dimensions):
            raise ValueError("review dimension names must be non-empty")
        for name, value in self.dimensions.items():
            numeric = float(value)
            if not 0.0 <= numeric <= 1.0:
                raise ValueError(f"review dimension must be in [0, 1]: {name}")
        if self.approved and self.findings:
            raise ValueError("a review with unresolved findings cannot be approved")
        if self.approved and not self.evidence_refs:
            raise ValueError("an approval requires evidence references")


@dataclass(frozen=True)
class PersonaWorkflow:
    """Injected agent callbacks for a two-stage independent review workflow."""

    roster: PersonaRoster
    plan: Callable[[Any, list[Any]], EngineeringPlan]
    review_plan: Callable[[Any, EngineeringPlan], PersonaReview]
    review_implementation: Callable[[Any, EngineeringPlan, Mapping[str, Any]], PersonaReview]

    def validate(self) -> None:
        self.roster.validate()
        if not callable(self.plan) or not callable(self.review_plan) or not callable(self.review_implementation):
            raise ValueError("all planning and independent review callbacks are required")


def require_approved_review(review: PersonaReview, *, reviewer: AgentPersona, target: AgentPersona) -> None:
    review.validate(reviewer=reviewer, target=target)
    if not review.approved:
        detail = "; ".join(review.findings) or "review did not approve the artifact"
        raise ValueError(detail)
