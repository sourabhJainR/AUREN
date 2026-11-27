"""Measured paired optimizer campaign aggregation, without promotion authority."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .quality_optimizer_contract import verify_paired_run_identity

METRICS = ("input_tokens", "output_tokens", "latency_ms", "cost_usd")


@dataclass(frozen=True)
class PairedTrial:
    trial_id: str
    baseline: Mapping[str, object]
    optimized: Mapping[str, object]
    baseline_quality: Mapping[str, object]
    optimized_quality: Mapping[str, object]


def _quality(receipt: Mapping[str, object]) -> float:
    if receipt.get("status") != "passed":
        raise ValueError("quality receipt must have passed status")
    score = receipt.get("weighted_score")
    if (
        type(score) not in (int, float)
        or not math.isfinite(float(score))
        or not 0 <= score <= 100
    ):
        raise ValueError("invalid quality score")
    return float(score)


def _metrics(run: Mapping[str, object]) -> tuple[dict[str, float], int]:
    measured: dict[str, float] = {}
    for key in METRICS:
        value = run.get(key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ValueError("missing or invalid measured metric: " + key)
        measured[key] = float(value)

    violations = run.get("policy_violations")
    if type(violations) is not int or violations < 0:
        raise ValueError("invalid policy violation count")
    return measured, violations


def _validate_thresholds(minimum_quality: float, max_quality_regression: float) -> None:
    for name, value in (
        ("minimum_quality", minimum_quality),
        ("max_quality_regression", max_quality_regression),
    ):
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ValueError(name + " must be a finite number")
    if not 0 <= minimum_quality <= 100:
        raise ValueError("minimum_quality must be between 0 and 100")
    if not 0 <= max_quality_regression <= 100:
        raise ValueError("max_quality_regression must be between 0 and 100")


def analyze_campaign(
    trials: Sequence[PairedTrial],
    *,
    minimum_quality: float = 0,
    max_quality_regression: float = 0,
) -> dict[str, object]:
    """Summarize paired evidence; eligibility is not promotion authorization."""
    _validate_thresholds(minimum_quality, max_quality_regression)
    if not trials:
        raise ValueError("campaign requires paired trials")

    trial_ids = [trial.trial_id for trial in trials]
    if (
        any(not isinstance(trial_id, str) or not trial_id.strip() for trial_id in trial_ids)
        or len(set(trial_ids)) != len(trial_ids)
    ):
        raise ValueError("trial IDs must be non-empty and unique")

    deltas = {key: [] for key in METRICS}
    quality_deltas: list[float] = []
    violation_deltas: list[int] = []
    details: list[dict[str, object]] = []
    reasons: set[str] = set()

    for trial in trials:
        verify_paired_run_identity(trial.baseline, trial.optimized)
        baseline_metrics, baseline_violations = _metrics(trial.baseline)
        optimized_metrics, optimized_violations = _metrics(trial.optimized)
        baseline_quality = _quality(trial.baseline_quality)
        optimized_quality = _quality(trial.optimized_quality)

        if (
            trial.baseline_quality.get("task_digest") != trial.baseline.get("task_digest")
            or trial.optimized_quality.get("task_digest") != trial.optimized.get("task_digest")
        ):
            raise ValueError("rubric receipt task mismatch")

        for key in METRICS:
            deltas[key].append(baseline_metrics[key] - optimized_metrics[key])

        quality_delta = optimized_quality - baseline_quality
        violation_delta = optimized_violations - baseline_violations
        quality_deltas.append(quality_delta)
        violation_deltas.append(violation_delta)

        floor_met = optimized_quality >= minimum_quality
        regression_allowed = quality_delta >= -max_quality_regression
        violations_not_increased = violation_delta <= 0
        if not floor_met:
            reasons.add("optimized_quality_below_floor")
        if not regression_allowed:
            reasons.add("per_trial_quality_regression_exceeded")
        if not violations_not_increased:
            reasons.add("policy_violations_increased")

        details.append({
            "trial_id": trial.trial_id,
            "quality_delta": quality_delta,
            "policy_violation_delta": violation_delta,
            "quality_floor_met": floor_met,
            "quality_regression_allowed": regression_allowed,
            "policy_violations_not_increased": violations_not_increased,
        })

    def stats(values: Sequence[float]) -> dict[str, object]:
        mean = sum(values) / len(values)
        standard_error = (
            math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
            / math.sqrt(len(values))
            if len(values) > 1
            else None
        )
        return {"mean": mean, "standard_error": standard_error, "observations": len(values)}

    metrics = {key: stats(values) for key, values in deltas.items()}
    metrics["quality_delta"] = stats(quality_deltas)
    metrics["policy_violation_delta"] = stats(violation_deltas)

    eligible = not reasons
    return {
        "trial_count": len(trials),
        "paired_trials": details,
        "metrics": metrics,
        "promotion_eligible": eligible,
        "eligibility_reasons": sorted(reasons),
        "limitations": (
            "Descriptive paired evidence only; no independent evaluation, causality proof, "
            "or promotion authorization."
        ),
    }
