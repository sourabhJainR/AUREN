"""Framework-neutral agent/workflow contract and cross-adapter conformance gates.

This contract describes the semantics AUREN expects from any adapter. It does not
claim that a framework satisfies the contract until its runtime receipts pass the
same benchmark cases and trust-boundary checks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


SPEC_VERSION = "auren-agent-workflow-v1"
CRITICAL_GATES = (
    "authorization_enforced",
    "bounded_termination",
    "evidence_complete",
    "recovery_verified",
    "holdout_passed",
)


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")


@dataclass(frozen=True)
class AgentContract:
    agent_id: str
    role: str
    capabilities: tuple[str, ...]
    input_schema: Mapping[str, str] = field(default_factory=dict)
    output_schema: Mapping[str, str] = field(default_factory=dict)
    max_handoffs: int = 3

    def validate(self) -> None:
        _required(self.agent_id, "agent_id")
        _required(self.role, "role")
        if type(self.max_handoffs) is not int or self.max_handoffs < 0:
            raise ValueError("max_handoffs must be a non-negative integer")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        for capability in self.capabilities:
            _required(capability, "capability")
        for schema_name, schema in (("input_schema", self.input_schema), ("output_schema", self.output_schema)):
            if not isinstance(schema, Mapping):
                raise ValueError(f"{schema_name} must be a mapping")
            for key, value in schema.items():
                _required(key, f"{schema_name} key")
                _required(value, f"{schema_name} value")


@dataclass(frozen=True)
class WorkflowContract:
    workflow_id: str
    agents: tuple[AgentContract, ...]
    edges: tuple[tuple[str, str], ...]
    allowed_actions: tuple[str, ...]
    required_evidence: tuple[str, ...]
    max_steps: int = 100
    max_attempts: int = 8
    deadline_seconds: float = 900.0
    max_cost_units: float = 100.0
    require_human_approval_for: tuple[str, ...] = ("merge", "release", "deploy")

    def validate(self) -> None:
        _required(self.workflow_id, "workflow_id")
        if not self.agents:
            raise ValueError("at least one agent is required")
        for agent in self.agents:
            agent.validate()
        ids = [agent.agent_id for agent in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agent ids must be unique")
        known = set(ids)
        for edge in self.edges:
            if len(edge) != 2 or edge[0] not in known or edge[1] not in known:
                raise ValueError("edges must reference known agents")
            if edge[0] == edge[1]:
                raise ValueError("self edges require an explicit bounded-loop construct")
        if len(set(self.edges)) != len(self.edges):
            raise ValueError("edges must be unique")
        for collection_name, values in (
            ("allowed_actions", self.allowed_actions),
            ("required_evidence", self.required_evidence),
            ("require_human_approval_for", self.require_human_approval_for),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{collection_name} must be unique")
            for value in values:
                _required(value, collection_name)
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        if not self.required_evidence:
            raise ValueError("required_evidence must not be empty")
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for name, value in (("deadline_seconds", self.deadline_seconds), ("max_cost_units", self.max_cost_units)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "spec_version": SPEC_VERSION,
            "workflow_id": self.workflow_id,
            "agents": [asdict(agent) for agent in self.agents],
            "edges": [list(edge) for edge in self.edges],
            "allowed_actions": list(self.allowed_actions),
            "required_evidence": list(self.required_evidence),
            "max_steps": self.max_steps,
            "max_attempts": self.max_attempts,
            "deadline_seconds": self.deadline_seconds,
            "max_cost_units": self.max_cost_units,
            "require_human_approval_for": list(self.require_human_approval_for),
        }

    def digest(self) -> str:
        return _digest(self.as_dict())


@dataclass(frozen=True)
class AdapterObservation:
    """Normalized run receipt from any framework adapter; values must be measured."""

    adapter: str
    benchmark_digest: str
    case_ids: tuple[str, ...]
    passed_case_ids: tuple[str, ...]
    holdout_case_ids: tuple[str, ...]
    passed_holdout_case_ids: tuple[str, ...]
    authorization_enforced: bool
    bounded_termination: bool
    recovery_verified: bool
    evidence_refs: tuple[str, ...]
    unauthorized_action_count: int = 0
    unbounded_retry_count: int = 0
    duration_seconds: float = 0.0
    cost_units: float = 0.0
    error_count: int = 0

    def validate(self) -> None:
        _required(self.adapter, "adapter")
        _required(self.benchmark_digest, "benchmark_digest")
        if not self.case_ids or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids must be non-empty and unique")
        if not set(self.passed_case_ids).issubset(self.case_ids):
            raise ValueError("passed cases must belong to case_ids")
        if not set(self.holdout_case_ids).issubset(self.case_ids):
            raise ValueError("holdout cases must belong to case_ids")
        if not set(self.passed_holdout_case_ids).issubset(self.holdout_case_ids):
            raise ValueError("passed holdout cases must belong to holdout_case_ids")
        if not set(self.passed_holdout_case_ids).issubset(self.passed_case_ids):
            raise ValueError("passed holdout cases must also be passed cases")
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or any(not x.strip() for x in self.evidence_refs):
            raise ValueError("evidence_refs must be unique and non-empty")
        for name, value in (
            ("unauthorized_action_count", self.unauthorized_action_count),
            ("unbounded_retry_count", self.unbounded_retry_count),
            ("error_count", self.error_count),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name, value in (("duration_seconds", self.duration_seconds), ("cost_units", self.cost_units)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class ConformanceResult:
    adapter: str
    accepted: bool
    reasons: tuple[str, ...]
    overall_pass_rate: float
    holdout_pass_rate: float
    evidence_coverage: float
    duration_seconds: float
    cost_units: float
    score: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_adapter(
    workflow: WorkflowContract,
    observation: AdapterObservation,
    *,
    expected_benchmark_digest: str,
    minimum_overall_pass_rate: float = 0.80,
    minimum_holdout_pass_rate: float = 0.80,
) -> ConformanceResult:
    """Apply common gates to one adapter's receipt; never infers missing evidence."""
    workflow.validate()
    observation.validate()
    for name, value in (
        ("minimum_overall_pass_rate", minimum_overall_pass_rate),
        ("minimum_holdout_pass_rate", minimum_holdout_pass_rate),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1")

    reasons: list[str] = []
    if observation.benchmark_digest != expected_benchmark_digest:
        reasons.append("benchmark_mismatch")
    if not observation.authorization_enforced:
        reasons.append("authorization_not_enforced")
    if not observation.bounded_termination or observation.unbounded_retry_count:
        reasons.append("termination_not_bounded")
    if not observation.recovery_verified:
        reasons.append("recovery_not_verified")
    if observation.unauthorized_action_count:
        reasons.append("unauthorized_actions_observed")
    if observation.error_count:
        reasons.append("runtime_errors_observed")
    missing = sorted(set(workflow.required_evidence) - set(observation.evidence_refs))
    if missing:
        reasons.append("required_evidence_missing:" + ",".join(missing))
    if not observation.holdout_case_ids:
        reasons.append("holdout_cases_missing")
    if observation.duration_seconds > workflow.deadline_seconds:
        reasons.append("deadline_exceeded")
    if observation.cost_units > workflow.max_cost_units:
        reasons.append("cost_budget_exceeded")

    overall = len(observation.passed_case_ids) / len(observation.case_ids)
    holdout = (
        len(observation.passed_holdout_case_ids) / len(observation.holdout_case_ids)
        if observation.holdout_case_ids else 0.0
    )
    if overall < minimum_overall_pass_rate:
        reasons.append("overall_threshold_not_met")
    if holdout < minimum_holdout_pass_rate:
        reasons.append("holdout_threshold_not_met")
    evidence_coverage = len(set(workflow.required_evidence) & set(observation.evidence_refs)) / len(workflow.required_evidence)
    # Score is descriptive, never a substitute for any hard gate above.
    score = max(0.0, min(1.0, 0.65 * overall + 0.25 * holdout + 0.10 * evidence_coverage))
    return ConformanceResult(
        adapter=observation.adapter,
        accepted=not reasons,
        reasons=tuple(reasons),
        overall_pass_rate=overall,
        holdout_pass_rate=holdout,
        evidence_coverage=evidence_coverage,
        duration_seconds=float(observation.duration_seconds),
        cost_units=float(observation.cost_units),
        score=score,
    )


def compare_adapters(
    workflow: WorkflowContract,
    observations: Iterable[AdapterObservation],
    *,
    expected_benchmark_digest: str,
    minimum_overall_pass_rate: float = 0.80,
    minimum_holdout_pass_rate: float = 0.80,
) -> tuple[ConformanceResult, ...]:
    """Evaluate adapters on the same sealed benchmark digest and stable-sort results."""
    results = [
        assess_adapter(
            workflow, observation,
            expected_benchmark_digest=expected_benchmark_digest,
            minimum_overall_pass_rate=minimum_overall_pass_rate,
            minimum_holdout_pass_rate=minimum_holdout_pass_rate,
        )
        for observation in observations
    ]
    names = [result.adapter for result in results]
    if len(names) != len(set(names)):
        raise ValueError("adapter names must be unique in a comparison")
    return tuple(sorted(results, key=lambda result: (not result.accepted, -result.score, result.duration_seconds, result.cost_units, result.adapter)))


__all__ = [
    "SPEC_VERSION", "AgentContract", "WorkflowContract", "AdapterObservation",
    "ConformanceResult", "assess_adapter", "compare_adapters",
]
