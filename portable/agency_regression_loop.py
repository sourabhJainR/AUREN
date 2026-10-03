"""Automatic trace-driven regression execution and release decision loop for AUREN v14."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agency_observability import Dataset, ExperimentResult, Trace, run_experiment


RegressionFn = Callable[[Trace, Any], Any]
RegressionJudge = Callable[[Any, Any, Any], Mapping[str, float]]


@dataclass(frozen=True)
class RegressionPlan:
    """Versioned regression contract executed by AUREN after an engineering trace."""

    dataset: Dataset
    execute_case: RegressionFn
    judge: RegressionJudge
    threshold: float = 0.8
    name: str = "trace-regression"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("regression name is required")
        if not 0 <= self.threshold <= 1:
            raise ValueError("regression threshold must be between 0 and 1")


@dataclass(frozen=True)
class RegressionRun:
    """AUREN-owned regression run identity plus its immutable evaluation result."""

    regression_id: str
    trace_id: str
    result: ExperimentResult

    @property
    def passed(self) -> bool:
        return self.result.passed

    def as_dict(self) -> dict[str, Any]:
        return {
            "regression_id": self.regression_id,
            "trace_id": self.trace_id,
            "dataset": self.result.dataset,
            "dataset_version": self.result.dataset_version,
            "dataset_digest": self.result.dataset_digest,
            "scores": dict(self.result.scores),
            "passed": self.result.passed,
            "failures": list(self.result.failures),
        }


def execute_regression(trace: Trace, plan: RegressionPlan, regression_id: str | None = None) -> RegressionRun:
    """Create and execute a regression run using the completed trace as its parent context."""
    if not trace.trace_id:
        raise ValueError("trace must have a trace_id")
    run_id = (regression_id or f"reg-{trace.trace_id[:12]}-{uuid4().hex[:8]}").strip()
    if not run_id:
        raise ValueError("regression_id must not be empty")

    result = run_experiment(
        plan.dataset,
        lambda value: plan.execute_case(trace, value),
        lambda item, output: plan.judge(trace, item, output),
        threshold=plan.threshold,
    )
    return RegressionRun(run_id, trace.trace_id, result)


@dataclass(frozen=True)
class PromotionPolicy:
    """Conservative release policy: regression and hard gates must pass before promotion."""

    quality_threshold: float = 0.90
    require_regression: bool = True
    promote_on_pass: bool = True


@dataclass(frozen=True)
class PromotionDecision:
    action: str
    reason: str
    quality: float
    regression_passed: bool
    regression_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "quality": self.quality,
            "regression_passed": self.regression_passed,
            "regression_id": self.regression_id,
        }


def decide_promotion(
    *,
    quality: float,
    hard_gates: Mapping[str, bool],
    regression: RegressionRun,
    policy: PromotionPolicy = PromotionPolicy(),
) -> PromotionDecision:
    """Feed quality + regression evidence into shadow/canary/promote/rollback policy."""
    quality = max(0.0, min(1.0, float(quality)))
    regression_ok = regression.passed if policy.require_regression else True
    gates_ok = all(bool(v) for v in hard_gates.values())
    if not gates_ok:
        return PromotionDecision("rollback", "hard gate failed", quality, regression.passed, regression.regression_id)
    if not regression_ok:
        return PromotionDecision("rollback", "regression failed", quality, regression.passed, regression.regression_id)
    if quality < policy.quality_threshold:
        return PromotionDecision("shadow", "quality below promotion threshold", quality, regression.passed, regression.regression_id)
    if policy.promote_on_pass:
        return PromotionDecision("promote", "quality and regression passed", quality, regression.passed, regression.regression_id)
    return PromotionDecision("canary", "quality and regression passed; staged rollout required", quality, regression.passed, regression.regression_id)


def run_trace_regression_loop(
    trace: Trace,
    plan: RegressionPlan,
    *,
    quality: float,
    hard_gates: Mapping[str, bool],
    policy: PromotionPolicy = PromotionPolicy(),
    regression_id: str | None = None,
) -> tuple[RegressionRun, PromotionDecision]:
    """Close the AUREN loop: trace -> regression -> decision."""
    run = execute_regression(trace, plan, regression_id=regression_id)
    decision = decide_promotion(quality=quality, hard_gates=hard_gates, regression=run, policy=policy)
    return run, decision
