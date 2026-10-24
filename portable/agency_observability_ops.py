"""Reviewed trace-to-dataset workflows, evaluator calibration, drift alerts and safe summaries."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from .agency_observability import Dataset, DatasetItem, _safe


@dataclass(frozen=True)
class ReviewedTraceExample:
    reviewer_id: str
    evidence_ref: str
    minimized_input: Any
    expected_behavior: Any
    approved: bool
    surprising: bool = False
    sealed_holdout: bool = False


def reviewed_trace_to_dataset_item(trace: Any, review: ReviewedTraceExample) -> DatasetItem:
    """Create a regression example only from an explicitly reviewed, non-holdout trace."""
    if not review.approved:
        raise ValueError("trace-to-dataset conversion requires explicit review approval")
    if review.sealed_holdout:
        raise ValueError("sealed holdout traces cannot be exposed to worker-accessible datasets")
    if not review.reviewer_id.strip() or not review.evidence_ref.strip():
        raise ValueError("reviewer identity and review evidence reference are required")
    if not str(getattr(trace, "trace_id", "")).strip() or getattr(trace, "end", None) is None:
        raise ValueError("only completed traces can be reviewed into a dataset")
    if getattr(trace, "status", "unknown") == "ok" and not review.surprising:
        raise ValueError("a successful trace requires an explicit surprising-behavior review")
    if review.minimized_input is None or review.expected_behavior is None:
        raise ValueError("a minimized input and expected behavior are required")
    event = trace.as_dict()
    return DatasetItem(
        input=_safe(review.minimized_input),
        expected=_safe(review.expected_behavior),
        metadata={
            "source_trace_id": trace.trace_id,
            "source_event_digest": event.get("event_digest", ""),
            "reviewer_id": review.reviewer_id,
            "review_evidence_ref": review.evidence_ref,
            "reviewed": True,
            "sealed_holdout": False,
        },
    )


class DatasetRegistry:
    """In-memory immutable name/version registry; durable deployments must persist it."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, Dataset]] = {}

    def register(self, dataset: Dataset) -> Dataset:
        if not dataset.name.strip() or not dataset.version.strip():
            raise ValueError("dataset name and version are required")
        versions = self._versions.setdefault(dataset.name, {})
        existing = versions.get(dataset.version)
        if existing is not None and existing.digest != dataset.digest:
            raise ValueError(f"dataset version is immutable: {dataset.name}:{dataset.version}")
        versions[dataset.version] = existing or dataset
        return versions[dataset.version]

    def get(self, name: str, version: str) -> Dataset:
        try:
            return self._versions[name][version]
        except KeyError as exc:
            raise KeyError(f"dataset version not found: {name}:{version}") from exc

    def append_reviewed(
        self,
        *,
        name: str,
        version: str,
        base_version: str | None,
        trace: Any,
        review: ReviewedTraceExample,
    ) -> Dataset:
        """Create a new immutable version; never mutate an existing dataset revision."""
        existing_items: tuple[DatasetItem, ...] = ()
        if base_version is not None:
            existing_items = self.get(name, base_version).items
        new_item = reviewed_trace_to_dataset_item(trace, review)
        return self.register(Dataset(name, version, existing_items + (new_item,)))


@dataclass(frozen=True)
class CalibrationSample:
    score: float
    expected_pass: bool
    evidence_ref: str


@dataclass(frozen=True)
class CalibrationResult:
    status: str
    threshold: float | None
    training_count: int
    validation_count: int
    training_false_pass_rate: float | None
    validation_false_pass_rate: float | None
    validation_pass_recall: float | None
    reason: str

    @property
    def eligible_for_guardrail(self) -> bool:
        # Calibration is necessary but not sufficient; a separate approval is required.
        return False


@dataclass(frozen=True)
class GuardrailAuthorization:
    metric: str
    threshold: float
    reviewer_id: str
    evidence_ref: str
    calibration_status: str


def _rates(samples: tuple[CalibrationSample, ...], threshold: float) -> tuple[float, float]:
    negatives = [sample for sample in samples if not sample.expected_pass]
    positives = [sample for sample in samples if sample.expected_pass]
    false_pass_rate = sum(sample.score >= threshold for sample in negatives) / len(negatives) if negatives else 1.0
    pass_recall = sum(sample.score >= threshold for sample in positives) / len(positives) if positives else 0.0
    return false_pass_rate, pass_recall


def calibrate_evaluator(
    training: Iterable[CalibrationSample],
    validation: Iterable[CalibrationSample],
    *,
    min_training: int = 30,
    min_validation: int = 20,
    max_false_pass_rate: float = 0.02,
    min_pass_recall: float = 0.70,
) -> CalibrationResult:
    """Calibrate on labeled training cases, then independently validate the chosen threshold."""
    train = tuple(training)
    valid = tuple(validation)
    if not 0 <= max_false_pass_rate < 1 or not 0 < min_pass_recall <= 1:
        raise ValueError("invalid calibration targets")
    for collection in (train, valid):
        for sample in collection:
            if not sample.evidence_ref.strip() or not math.isfinite(sample.score) or not 0 <= sample.score <= 1:
                raise ValueError("calibration samples require evidence and finite scores in [0, 1]")
    if len(train) < min_training or len(valid) < min_validation:
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "insufficient labeled samples")
    if any(len({sample.expected_pass for sample in collection}) < 2 for collection in (train, valid)):
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "both classes are required in training and validation")

    candidates = sorted({0.0, 1.0, *(sample.score for sample in train)})
    chosen = None
    train_false = None
    for threshold in candidates:
        false_pass, recall = _rates(train, threshold)
        if false_pass <= max_false_pass_rate and recall >= min_pass_recall:
            chosen, train_false = threshold, false_pass
            break
    if chosen is None:
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "no training threshold satisfies safety and recall targets")
    valid_false, valid_recall = _rates(valid, chosen)
    if valid_false > max_false_pass_rate or valid_recall < min_pass_recall:
        return CalibrationResult("unscored", None, len(train), len(valid), train_false, valid_false, valid_recall, "independent validation did not meet calibration targets")
    return CalibrationResult("calibrated", chosen, len(train), len(valid), train_false, valid_false, valid_recall, "validated; separate guardrail approval still required")


def authorize_calibrated_guardrail(
    metric: str,
    result: CalibrationResult,
    *,
    reviewer_id: str,
    evidence_ref: str,
) -> GuardrailAuthorization:
    if result.status != "calibrated" or result.threshold is None:
        raise ValueError("only independently validated calibration can be considered for guardrail approval")
    if not metric.strip() or not reviewer_id.strip() or not evidence_ref.strip():
        raise ValueError("metric, reviewer identity and approval evidence are required")
    return GuardrailAuthorization(metric, result.threshold, reviewer_id, evidence_ref, result.status)


@dataclass(frozen=True)
class MetricObservation:
    metric: str
    domain: str
    value: float
    run_id: str
    evidence_ref: str
    timestamp: float
    risk_tier: str = "normal"


@dataclass(frozen=True)
class DriftPolicy:
    baseline_count: int = 20
    recent_count: int = 10
    minimum_absolute_shift: float = 0.10
    safety_floor: float | None = None

    def __post_init__(self) -> None:
        if self.baseline_count < 2 or self.recent_count < 2:
            raise ValueError("drift windows must contain at least two observations")
        if self.minimum_absolute_shift < 0:
            raise ValueError("minimum_absolute_shift must be non-negative")


def evaluate_drift(observations: Iterable[MetricObservation], policy: DriftPolicy = DriftPolicy()) -> dict[str, Any]:
    points = tuple(sorted(observations, key=lambda point: point.timestamp))
    if any(not point.metric.strip() or not point.domain.strip() or not point.run_id.strip()
           or not point.evidence_ref.strip() or not math.isfinite(point.value) for point in points):
        raise ValueError("drift observations require provenance and finite values")
    needed = policy.baseline_count + policy.recent_count
    if len(points) < needed:
        return {"status": "insufficient_data", "count": len(points), "required": needed, "alert_reasons": []}
    selected = points[-needed:]
    baseline = selected[:policy.baseline_count]
    recent = selected[policy.baseline_count:]
    if len({point.metric for point in selected}) != 1 or len({point.domain for point in selected}) != 1:
        raise ValueError("a drift window must contain one metric and one domain")
    baseline_mean, recent_mean = mean(point.value for point in baseline), mean(point.value for point in recent)
    shift = recent_mean - baseline_mean
    reasons = []
    if abs(shift) >= policy.minimum_absolute_shift:
        reasons.append("distribution_shift")
    if policy.safety_floor is not None and recent_mean < policy.safety_floor:
        reasons.append("safety_floor_breached")
    return {
        "status": "alert" if reasons else "ok",
        "metric": recent[0].metric,
        "domain": recent[0].domain,
        "baseline_count": len(baseline),
        "recent_count": len(recent),
        "baseline_mean": round(baseline_mean, 6),
        "recent_mean": round(recent_mean, 6),
        "absolute_shift": round(abs(shift), 6),
        "alert_reasons": reasons,
        "run_ids": [point.run_id for point in recent],
        "high_risk_observations": sum(point.risk_tier.lower() in {"high", "critical"} for point in selected),
    }


def observability_snapshot(path: Path, *, limit: int = 500) -> dict[str, Any]:
    """Return a safe aggregate view; never return raw prompts, span inputs or outputs."""
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    if not path.exists():
        return {"status": "unavailable", "reason": "trace_store_missing", "trace_count": 0}
    lines: deque[str] = deque(maxlen=limit)
    corrupt_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                lines.append(line)
    traces = []
    for line in lines:
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or not item.get("trace_id"):
                corrupt_lines += 1
                continue
            traces.append(item)
        except (json.JSONDecodeError, UnicodeDecodeError):
            corrupt_lines += 1
    phase_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    recent = []
    durations = []
    for trace in traces:
        status = str(trace.get("status", "unknown"))
        status_counts[status] += 1
        duration = trace.get("duration_ms")
        if isinstance(duration, (int, float)) and math.isfinite(duration):
            durations.append(float(duration))
        phases = []
        for span in trace.get("spans", ()):
            if not isinstance(span, dict):
                continue
            phase = str(span.get("phase", "unknown"))
            phase_counts[phase] += 1
            phases.append({"phase": phase, "status": str(span.get("status", "unknown"))})
            provider = span.get("provider")
            if isinstance(provider, str) and provider:
                provider_counts[provider] += 1
        metadata = trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}
        recent.append({
            "trace_id": str(trace.get("trace_id", "")),
            "run_id": str(trace.get("run_id", "")),
            "task_id": str(trace.get("task_id", "")),
            "status": status,
            "duration_ms": duration if isinstance(duration, (int, float)) and math.isfinite(duration) else None,
            "source_revision": trace.get("source_revision"),
            "phases": phases,
            "phase_coverage_gaps": metadata.get("phase_coverage_gaps", []),
            "review_mode": metadata.get("review_mode", "unknown"),
        })
    return {
        "status": "ok" if traces else ("corrupt" if corrupt_lines else "empty"),
        "trace_count": len(traces),
        "corrupt_line_count": corrupt_lines,
        "status_counts": dict(status_counts),
        "phase_counts": dict(phase_counts),
        "provider_counts": dict(provider_counts),
        "mean_duration_ms": round(mean(durations), 3) if durations else None,
        "recent_traces": recent[-100:],
        "raw_payloads_exposed": False,
    }
