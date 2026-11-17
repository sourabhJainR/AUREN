"""Reproducible paired AUREN-versus-direct engineering benchmark contracts.

This module validates submitted run receipts and summarizes paired evidence. It
never executes tasks or treats candidate self-assessment as independent review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import statistics
from typing import Iterable, Mapping


METRICS = (
    "elapsed_seconds", "active_user_seconds", "human_interventions",
    "input_tokens", "output_tokens", "tool_calls", "retries",
    "policy_violations", "escaped_defects", "requirement_coverage",
    "evidence_completeness", "quality_score",
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    benchmark_id: str
    version: str
    task_suite_digest: str
    task_family: str
    model_tier: str
    model_id: str
    provider_version: str
    context_budget_tokens: int
    acceptance_digest: str
    rubric_digest: str
    protocol_version: str = "auren-paired-benchmark-v1"

    def __post_init__(self) -> None:
        for name in ("benchmark_id", "version", "task_suite_digest", "task_family",
                     "model_tier", "model_id", "provider_version",
                     "acceptance_digest", "rubric_digest", "protocol_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.context_budget_tokens < 1:
            raise ValueError("context budget must be positive")
        if self.protocol_version != "auren-paired-benchmark-v1":
            raise ValueError("unsupported benchmark protocol version")

    @property
    def digest(self) -> str:
        return _digest({
            "benchmark_id": self.benchmark_id, "version": self.version,
            "task_suite_digest": self.task_suite_digest, "task_family": self.task_family,
            "model_tier": self.model_tier, "model_id": self.model_id,
            "provider_version": self.provider_version,
            "context_budget_tokens": self.context_budget_tokens,
            "acceptance_digest": self.acceptance_digest,
            "rubric_digest": self.rubric_digest,
            "protocol_version": self.protocol_version,
        })


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    run_id: str
    task_id: str
    task_digest: str
    mode: str
    manifest_digest: str
    executor_id: str
    evaluator_id: str
    evaluator_version: str
    rubric_receipt_digest: str
    rubric_status: str
    metrics: Mapping[str, int | float | bool | None]
    recovery_success: bool | None
    duplicate_or_stale_side_effects: int
    evidence_refs: tuple[str, ...]
    failed: bool = False

    def __post_init__(self) -> None:
        for name in ("run_id", "task_id", "task_digest", "manifest_digest",
                     "executor_id", "evaluator_id", "evaluator_version",
                     "rubric_receipt_digest"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.mode not in {"auren", "direct"}:
            raise ValueError("mode must be auren or direct")
        if self.recovery_success is not None and type(self.recovery_success) is not bool:
            raise ValueError("recovery_success must be a boolean or unknown")
        if self.evaluator_id == self.executor_id:
            raise ValueError("executor cannot act as independent evaluator")
        if self.rubric_status not in {"passed", "failed", "blocked", "unscored"}:
            raise ValueError("invalid rubric status")
        if not isinstance(self.metrics, Mapping):
            raise ValueError("metrics must be a mapping")
        unknown = set(self.metrics) - set(METRICS) - {"provider_cost"}
        if unknown:
            raise ValueError(f"unknown metrics: {sorted(unknown)}")
        for name, value in self.metrics.items():
            if value is None:
                continue
            if name in {"human_interventions", "input_tokens", "output_tokens",
                        "tool_calls", "retries", "policy_violations",
                        "escaped_defects", "duplicate_or_stale_side_effects"}:
                if type(value) is not int or value < 0:
                    raise ValueError(f"{name} must be a non-negative integer")
            elif name in {"requirement_coverage", "evidence_completeness", "quality_score"}:
                if type(value) not in (int, float) or not math.isfinite(float(value)) or not 0 <= value <= 100:
                    raise ValueError(f"{name} must be a finite value from 0 to 100")
            elif name == "provider_cost":
                if type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0:
                    raise ValueError("provider_cost must be a finite non-negative value")
            elif type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        if type(self.duplicate_or_stale_side_effects) is not int or self.duplicate_or_stale_side_effects < 0:
            raise ValueError("duplicate_or_stale_side_effects must be a non-negative integer")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence references must be non-empty strings")
        if not self.evidence_refs:
            raise ValueError("each run must include evidence references")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence references must be unique")
        if type(self.failed) is not bool:
            raise ValueError("failed must be a boolean")


def _metric(run: BenchmarkRun, name: str) -> float | None:
    value = run.metrics.get(name)
    return float(value) if value is not None else None


def compare_pair(
    manifest: BenchmarkManifest,
    *,
    baseline: BenchmarkRun,
    candidate: BenchmarkRun,
) -> dict[str, object]:
    """Compare one same-task pair; incomplete/failed runs remain visible."""
    if baseline.mode != "direct" or candidate.mode != "auren":
        raise ValueError("baseline must be direct and candidate must be auren")
    if baseline.manifest_digest != manifest.digest or candidate.manifest_digest != manifest.digest:
        raise ValueError("both runs must bind to the exact benchmark manifest")
    if baseline.task_digest != candidate.task_digest or baseline.task_id != candidate.task_id:
        raise ValueError("paired runs must use the same task identity and digest")
    if baseline.rubric_receipt_digest == candidate.rubric_receipt_digest:
        raise ValueError("paired runs require distinct, run-bound rubric receipts")
    failure_reasons = []
    for run in (baseline, candidate):
        if run.rubric_status != "passed":
            failure_reasons.append(run.mode + "_rubric_" + run.rubric_status)
        if run.failed:
            failure_reasons.append(run.mode + "_run_failed")
    deltas = {}
    for name in METRICS:
        left, right = _metric(baseline, name), _metric(candidate, name)
        deltas[name] = None if left is None or right is None else round(right - left, 4)
    base_cost, candidate_cost = _metric(baseline, "provider_cost"), _metric(candidate, "provider_cost")
    cost_delta = None if base_cost is None or candidate_cost is None else round(candidate_cost - base_cost, 8)
    base_policy = _metric(baseline, "policy_violations")
    candidate_policy = _metric(candidate, "policy_violations")
    base_defects = _metric(baseline, "escaped_defects")
    candidate_defects = _metric(candidate, "escaped_defects")
    base_quality = _metric(baseline, "quality_score")
    candidate_quality = _metric(candidate, "quality_score")
    base_evidence = _metric(baseline, "evidence_completeness")
    candidate_evidence = _metric(candidate, "evidence_completeness")
    safety_non_regression = (
        base_policy is not None and candidate_policy is not None
        and candidate_policy == 0 and candidate_policy <= base_policy
        and candidate.duplicate_or_stale_side_effects == 0
        and baseline.duplicate_or_stale_side_effects >= candidate.duplicate_or_stale_side_effects
        and base_defects is not None and candidate_defects is not None
        and candidate_defects == 0 and candidate_defects <= base_defects
    )
    quality_non_regression = (
        base_quality is not None and candidate_quality is not None
        and candidate_quality >= base_quality
        and base_evidence is not None and candidate_evidence is not None
        and candidate_evidence >= base_evidence
        and candidate.recovery_success is True
    )
    comparable = not failure_reasons
    result = {
        "task_id": baseline.task_id,
        "task_digest": baseline.task_digest,
        "manifest_digest": manifest.digest,
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "baseline_evaluator": baseline.evaluator_id,
        "candidate_evaluator": candidate.evaluator_id,
        "comparable": comparable,
        "failure_reasons": tuple(failure_reasons),
        "deltas_candidate_minus_baseline": deltas,
        "provider_cost_delta": cost_delta,
        "safety_non_regression": safety_non_regression,
        "quality_non_regression": quality_non_regression,
        "promotion_eligible": comparable and safety_non_regression and quality_non_regression,
    }
    result["pair_digest"] = _digest(result)
    return result


def summarize_pairs(manifest: BenchmarkManifest, pairs: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Summarize validated pair results without hiding failures or small samples."""
    rows = list(pairs)
    if not rows:
        raise ValueError("at least one validated pair is required")
    if any(row.get("manifest_digest") != manifest.digest for row in rows):
        raise ValueError("all pair results must bind to the same manifest")
    task_ids = [row.get("task_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in task_ids):
        raise ValueError("benchmark report requires non-empty task IDs")
    run_ids = []
    for row in rows:
        baseline_run_id = row.get("baseline_run_id")
        candidate_run_id = row.get("candidate_run_id")
        if (
            not isinstance(baseline_run_id, str) or not baseline_run_id
            or not isinstance(candidate_run_id, str) or not candidate_run_id
            or baseline_run_id == candidate_run_id
        ):
            raise ValueError("each benchmark pair requires distinct non-empty baseline and candidate run IDs")
        run_ids.extend((baseline_run_id, candidate_run_id))
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("benchmark reports cannot reuse a baseline or candidate run")
    for row in rows:
        claimed = row.get("pair_digest")
        payload = {key: value for key, value in row.items() if key != "pair_digest"}
        if not isinstance(claimed, str) or claimed != _digest(payload):
            raise ValueError("paired result digest is missing or invalid")
    numeric: dict[str, list[float]] = {name: [] for name in METRICS}
    for row in rows:
        deltas = row.get("deltas_candidate_minus_baseline")
        if not isinstance(deltas, Mapping):
            raise ValueError("pair result is missing metric deltas")
        if row.get("comparable") is True:
            for name in METRICS:
                value = deltas.get(name)
                if value is not None:
                    numeric[name].append(float(value))
    means = {name: round(statistics.mean(values), 4) if values else None for name, values in numeric.items()}
    medians = {name: round(statistics.median(values), 4) if values else None for name, values in numeric.items()}
    provider_cost_deltas = [
        float(row["provider_cost_delta"]) for row in rows
        if row.get("comparable") is True and row.get("provider_cost_delta") is not None
    ]
    metric_sample_counts = {name: len(values) for name, values in numeric.items()}
    metric_sample_counts["provider_cost"] = len(provider_cost_deltas)
    comparable_count = sum(row.get("comparable") is True for row in rows)
    metric_sample_coverage = {
        name: round(count / max(1, comparable_count), 4)
        for name, count in metric_sample_counts.items()
    }
    return {
        "schema_version": "auren-paired-benchmark-report-v1",
        "benchmark_id": manifest.benchmark_id,
        "manifest_digest": manifest.digest,
        "sample_size": len(rows),
        "unique_task_count": len(set(task_ids)),
        "comparable_pairs": sum(row.get("comparable") is True for row in rows),
        "failed_or_unscored_pairs": sum(row.get("comparable") is not True for row in rows),
        "promotion_eligible_pairs": sum(row.get("promotion_eligible") is True for row in rows),
        "pairs": rows,
        "mean_deltas_candidate_minus_baseline": means,
        "median_deltas_candidate_minus_baseline": medians,
        "metric_sample_counts": metric_sample_counts,
        "metric_sample_coverage": metric_sample_coverage,
        "provider_cost_delta_mean": (
            round(statistics.mean([
                float(row["provider_cost_delta"]) for row in rows
                if row.get("comparable") is True and row.get("provider_cost_delta") is not None
            ]), 8)
            if any(row.get("comparable") is True and row.get("provider_cost_delta") is not None for row in rows) else None
        ),
        "uncertainty": (
            "descriptive_only_small_sample" if sum(row.get("comparable") is True for row in rows) < 5
            else "paired_sample_summary_only_no_population_claim"
        ),
        "limitations": [
            "A report is only as trustworthy as the source run receipts and independent evaluator custody.",
            "Missing provider usage or pricing remains unknown, not zero.",
            "Repository CI and internal evaluator identity do not establish independent external validation.",
        ],
    }


__all__ = ["BenchmarkManifest", "BenchmarkRun", "compare_pair", "summarize_pairs"]
