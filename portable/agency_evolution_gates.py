"""Evidence-driven learning, adaptive quality controls, and recovery proof for AUREN.

This module supplies dependency-free control-plane primitives for Gates C/D/E.
It records proposals and evidence; it does not execute arbitrary candidates or
grant promotion authority. The trusted controller must enforce every decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


HYPOTHESIS_STATES = (
    "observed", "investigated", "hypothesized", "planned", "evaluated",
    "approved", "deployed", "verified", "rejected", "blocked",
    "inconclusive", "rolled_back",
)
_ALLOWED_TRANSITIONS = {
    "observed": {"investigated", "blocked", "rejected"},
    "investigated": {"hypothesized", "blocked", "rejected"},
    "hypothesized": {"planned", "blocked", "rejected"},
    "planned": {"evaluated", "blocked", "rejected"},
    "evaluated": {"approved", "rejected", "inconclusive", "blocked"},
    "approved": {"deployed", "blocked"},
    "deployed": {"verified", "rolled_back", "blocked"},
    "verified": set(),
    "rejected": set(),
    "blocked": {"investigated", "rejected"},
    "inconclusive": {"investigated", "rejected"},
    "rolled_back": {"investigated", "rejected"},
}
_FORBIDDEN_PROMOTION_GATES = {
    "evaluator_independent", "holdout_isolated", "evidence_integrity",
    "required_checks_passed", "rollback_proven", "approval_recorded",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


@dataclass(frozen=True)
class Signal:
    """A candidate learning signal. Signals are not ground-truth labels."""

    signal_id: str
    category: str
    summary: str
    source_ref: str
    observed_at: float = field(default_factory=time.time)
    severity: float = 0.5
    recurrence: float = 0.0
    confidence: float = 0.5
    expected_impact: float = 0.5
    estimated_cost: float = 0.5
    security_relevance: float = 0.0
    reversible: bool = True

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.category.strip() or not self.summary.strip():
            raise ValueError("signal_id, category and summary are required")
        if not self.source_ref.strip():
            raise ValueError("signal requires a source evidence reference")
        for name in ("severity", "recurrence", "confidence", "expected_impact", "estimated_cost", "security_relevance"):
            value = _finite(getattr(self, name), name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        _finite(self.observed_at, "observed_at")


def prioritize_signals(signals: Iterable[Signal]) -> tuple[Signal, ...]:
    """Order signals by risk-adjusted value, not frequency alone."""
    def score(signal: Signal) -> tuple[float, float, str]:
        value = (
            0.28 * signal.severity
            + 0.18 * signal.expected_impact
            + 0.16 * signal.recurrence
            + 0.12 * signal.confidence
            + 0.20 * signal.security_relevance
            + 0.06 * (0.0 if signal.reversible else 1.0)
            - 0.15 * signal.estimated_cost
        )
        return (value, signal.observed_at, signal.signal_id)
    return tuple(sorted(signals, key=score, reverse=True))


@dataclass(frozen=True)
class ImprovementHypothesis:
    """Version-bound proposal; a hypothesis cannot approve itself."""

    hypothesis_id: str
    problem: str
    evidence_refs: tuple[str, ...]
    proposed_change: str
    expected_benefit: str
    acceptance_criteria: tuple[str, ...]
    risk: str
    budget_units: int
    baseline_revision: str
    candidate_revision: str
    rollback_plan: str
    state: str = "observed"
    reviewer_id: str = ""
    decision_ref: str = ""
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        required = (self.hypothesis_id, self.problem, self.proposed_change, self.expected_benefit,
                    self.risk, self.baseline_revision, self.candidate_revision, self.rollback_plan)
        if any(not str(value).strip() for value in required):
            raise ValueError("hypothesis identity, problem, change, benefit, risk, revisions and rollback plan are required")
        if not self.evidence_refs or any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("hypothesis must cite source evidence")
        if not self.acceptance_criteria or any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("hypothesis must define acceptance criteria")
        if self.budget_units < 1:
            raise ValueError("budget_units must be positive")
        if self.state not in HYPOTHESIS_STATES:
            raise ValueError("unknown hypothesis state")
        _finite(self.updated_at, "updated_at")
        if self.state in {"approved", "deployed", "verified"} and (not self.reviewer_id.strip() or not self.decision_ref.strip()):
            raise ValueError("approval and later states require independent reviewer and decision evidence")

    def transition(self, state: str, *, reviewer_id: str = "", decision_ref: str = "") -> "ImprovementHypothesis":
        if state not in _ALLOWED_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"invalid hypothesis transition: {self.state} -> {state}")
        if state == "approved" and (not reviewer_id.strip() or not decision_ref.strip()):
            raise ValueError("approval requires an independent reviewer and decision reference")
        if state in {"deployed", "verified"} and (not reviewer_id.strip() or not decision_ref.strip()):
            raise ValueError("deployment and verification require reviewer and decision references")
        from dataclasses import replace
        return replace(self, state=state, reviewer_id=reviewer_id or self.reviewer_id,
                       decision_ref=decision_ref or self.decision_ref, updated_at=time.time())

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class ExperimentManifest:
    """Immutable experiment contract pinned to source, dataset and evaluator revisions."""

    experiment_id: str
    hypothesis_id: str
    baseline_revision: str
    candidate_revision: str
    dataset_digest: str
    evaluator_revision: str
    seed: int
    budget_units: int
    metrics: tuple[str, ...]
    holdout_reference: str = ""
    holdout_payload_included: bool = False

    def __post_init__(self) -> None:
        for name in ("experiment_id", "hypothesis_id", "baseline_revision", "candidate_revision",
                     "dataset_digest", "evaluator_revision"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.budget_units < 1 or not self.metrics or any(not metric.strip() for metric in self.metrics):
            raise ValueError("positive budget and named metrics are required")
        if self.holdout_payload_included:
            raise ValueError("sealed holdout payload must never be embedded in an experiment manifest")
        if self.holdout_reference and len(self.holdout_reference) > 256:
            raise ValueError("holdout reference is too long")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class DriftResult:
    metric: str
    sample_count: int
    observed_mean: float
    fixed_baseline: float
    adaptive_baseline: float
    absolute_delta: float
    adaptive_delta: float
    drift_detected: bool
    reason: str


def detect_drift(
    metric: str,
    observations: Iterable[float],
    *,
    fixed_baseline: float,
    adaptive_baseline: float,
    absolute_tolerance: float,
    adaptive_tolerance: float,
) -> DriftResult:
    """Compare operational drift with a fixed release reference and a bounded adaptive reference.

    The adaptive reference helps identify workload shifts; it never replaces the
    fixed baseline, so gradual degradation cannot normalize away a release regression.
    """
    if not metric.strip():
        raise ValueError("metric is required")
    values = [_finite(v, "observation") for v in observations]
    if not values:
        raise ValueError("at least one observation is required")
    fixed = _finite(fixed_baseline, "fixed_baseline")
    adaptive = _finite(adaptive_baseline, "adaptive_baseline")
    abs_tol = _finite(absolute_tolerance, "absolute_tolerance")
    adapt_tol = _finite(adaptive_tolerance, "adaptive_tolerance")
    if abs_tol < 0 or adapt_tol < 0:
        raise ValueError("drift tolerances must be non-negative")
    average = sum(values) / len(values)
    fixed_delta = average - fixed
    adaptive_delta = average - adaptive
    fixed_drift = abs(fixed_delta) > abs_tol
    adaptive_drift = abs(adaptive_delta) > adapt_tol
    reasons = []
    if fixed_drift:
        reasons.append("fixed-release-baseline")
    if adaptive_drift:
        reasons.append("adaptive-operational-baseline")
    return DriftResult(metric, len(values), average, fixed, adaptive, fixed_delta, adaptive_delta,
                       fixed_drift or adaptive_drift, ",".join(reasons) if reasons else "within-tolerance")


@dataclass(frozen=True)
class PromotionGateDecision:
    action: str
    missing_gates: tuple[str, ...]
    reason: str


def evaluate_promotion_gates(gates: Mapping[str, bool], *, candidate_passed: bool,
                             regression_passed: bool, risk_approved: bool) -> PromotionGateDecision:
    """Fail closed on missing, false or unrecognized trust gates."""
    missing = tuple(sorted(name for name in _FORBIDDEN_PROMOTION_GATES if gates.get(name) is not True))
    unknown_false = tuple(sorted(name for name, value in gates.items() if not isinstance(value, bool) or value is not True))
    if missing or unknown_false or not candidate_passed or not regression_passed or not risk_approved:
        reasons = []
        if missing:
            reasons.append("missing required trust gates: " + ", ".join(missing))
        if unknown_false:
            reasons.append("false or invalid gates: " + ", ".join(unknown_false))
        if not candidate_passed:
            reasons.append("candidate evaluation failed")
        if not regression_passed:
            reasons.append("regression failed")
        if not risk_approved:
            reasons.append("risk approval absent")
        return PromotionGateDecision("block", missing, "; ".join(reasons))
    return PromotionGateDecision("eligible_for_controller_approval", (), "evidence gates passed; controller approval is still required")


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    event_id: str
    work_id: str
    state: str
    payload: Mapping[str, Any]
    previous_digest: str
    digest: str
    timestamp: float


class ImprovementJournal:
    """Append-only, hash-chained JSONL journal with idempotent event IDs.

    The journal is evidence storage, not a distributed lock or an authority source.
    The trusted supervisor must serialize state-changing operations and reconcile
    remote effects before retrying them.
    """

    GENESIS = "0" * 64

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> tuple[JournalEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[JournalEvent] = []
        previous = self.GENESIS
        seen: set[str] = set()
        with self.path.open("r", encoding="utf-8") as handle:
            for expected_sequence, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError("journal contains an empty line")
                raw = json.loads(line)
                supplied_digest = raw.pop("digest", None)
                if raw.get("sequence") != expected_sequence:
                    raise ValueError("journal sequence gap or reordering")
                if raw.get("previous_digest") != previous:
                    raise ValueError("journal hash-chain mismatch")
                event_id = str(raw.get("event_id", ""))
                if not event_id or event_id in seen:
                    raise ValueError("journal event ID is missing or duplicated")
                expected_digest = _digest(raw)
                if supplied_digest != expected_digest:
                    raise ValueError("journal event digest mismatch")
                event = JournalEvent(**raw, digest=supplied_digest)
                events.append(event)
                previous = supplied_digest
                seen.add(event_id)
        return tuple(events)

    def append(self, *, event_id: str, work_id: str, state: str, payload: Mapping[str, Any]) -> JournalEvent:
        if not event_id.strip() or not work_id.strip() or not state.strip():
            raise ValueError("event_id, work_id and state are required")
        safe_payload = json.loads(_canonical(dict(payload)))
        with self._lock:
            events = self.read()
            for event in events:
                if event.event_id == event_id:
                    if event.work_id == work_id and event.state == state and _canonical(event.payload) == _canonical(safe_payload):
                        return event
                    raise ValueError("event ID already exists with conflicting content")
            previous = events[-1].digest if events else self.GENESIS
            body = {
                "sequence": len(events) + 1,
                "event_id": event_id,
                "work_id": work_id,
                "state": state,
                "payload": safe_payload,
                "previous_digest": previous,
                "timestamp": time.time(),
            }
            event = JournalEvent(**body, digest=_digest(body))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = _canonical({**body, "digest": event.digest}) + "\n"
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "a", encoding="utf-8", newline="") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                # fdopen owns descriptor after successful construction.
                raise
            return event



def extract_signals_from_trace(
    trace: Mapping[str, Any],
    *,
    low_score_threshold: float = 0.6,
    slow_trace_ms: float = 30_000.0,
) -> tuple[Signal, ...]:
    """Convert trace outcomes into candidate signals without treating them as labels.

    Only stable IDs, statuses and metric values are copied. Prompts, inputs, outputs,
    and arbitrary metadata are deliberately excluded from signal summaries.
    """
    low_score = _finite(low_score_threshold, "low_score_threshold")
    slow_ms = _finite(slow_trace_ms, "slow_trace_ms")
    if not 0.0 <= low_score <= 1.0 or slow_ms < 0:
        raise ValueError("low_score_threshold must be in [0, 1] and slow_trace_ms non-negative")
    trace_id = str(trace.get("trace_id", "")).strip()
    if not trace_id:
        raise ValueError("trace must include a stable trace_id")
    source_ref = f"trace:{trace_id}"
    observed_at = _finite(trace.get("start", time.time()), "trace.start")
    signals: list[Signal] = []

    def add(kind: str, summary: str, *, severity: float, impact: float,
            confidence: float = 0.9, recurrence: float = 0.0,
            security_relevance: float = 0.0, cost: float = 0.2) -> None:
        identity = _digest({"trace_id": trace_id, "kind": kind, "summary": summary})[:24]
        signals.append(Signal(
            signal_id=f"sig-{identity}", category=kind, summary=summary,
            source_ref=source_ref, observed_at=observed_at, severity=severity,
            recurrence=recurrence, confidence=confidence, expected_impact=impact,
            estimated_cost=cost, security_relevance=security_relevance,
        ))

    status = str(trace.get("status", "unknown")).lower()
    if status not in {"ok", "success", "completed"}:
        add("task-outcome-failure", f"trace ended with status={status}",
            severity=0.85, impact=0.8, security_relevance=0.25 if status in {"denied", "unauthorized"} else 0.0)

    for span in trace.get("spans", ()) or ():
        if not isinstance(span, Mapping):
            continue
        span_id = str(span.get("span_id", "unknown"))
        span_status = str(span.get("status", "unknown")).lower()
        if span_status not in {"ok", "success", "completed", "running"}:
            add("span-outcome-failure", f"span={span_id} status={span_status}",
                severity=0.75, impact=0.65,
                security_relevance=0.35 if span_status in {"denied", "unauthorized", "policy_rejected"} else 0.0)
    for score in trace.get("scores", ()) or ():
        if not isinstance(score, Mapping):
            continue
        try:
            value = _finite(score.get("value"), "trace score")
        except (TypeError, ValueError):
            continue
        if not 0.0 <= value <= 1.0:
            continue
        if value < low_score:
            name = str(score.get("name", "unnamed"))[:80]
            add("low-evaluation-score", f"score={name} below configured threshold",
                severity=min(1.0, 1.0 - value), impact=0.7, confidence=0.8)
    duration = trace.get("duration_ms")
    if duration is not None:
        duration_ms = _finite(duration, "trace.duration_ms")
        if duration_ms >= slow_ms and slow_ms > 0:
            add("slow-trace", "trace duration exceeded configured latency threshold",
                severity=0.45, impact=0.4, confidence=0.95, cost=0.5)
    for regression in (trace.get("metadata", {}) or {}).get("regressions", ()) or ():
        if isinstance(regression, Mapping) and regression.get("passed") is False:
            regression_id = str(regression.get("regression_id", "unknown"))
            add("regression-failure", f"regression={regression_id} failed",
                severity=0.95, impact=0.95, confidence=1.0)
    return tuple(signals)


@dataclass(frozen=True)
class EvaluatorCalibration:
    evaluator_revision: str
    sample_count: int
    accuracy: float
    false_pass_rate: float | None
    false_fail_rate: float | None
    accepted: bool
    reason: str


def evaluate_evaluator_calibration(
    evaluator_revision: str,
    cases: Iterable[tuple[bool, bool]],
    *,
    minimum_samples: int = 20,
    minimum_accuracy: float = 0.95,
    maximum_false_pass_rate: float = 0.02,
) -> EvaluatorCalibration:
    """Measure evaluator decisions against independently adjudicated binary outcomes.

    Each pair is (trusted_expected_pass, evaluator_predicted_pass). The caller is
    responsible for proving that expected outcomes were independently adjudicated.
    """
    if not evaluator_revision.strip():
        raise ValueError("evaluator_revision is required")
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    min_accuracy = _finite(minimum_accuracy, "minimum_accuracy")
    max_false_pass = _finite(maximum_false_pass_rate, "maximum_false_pass_rate")
    if not 0.0 <= min_accuracy <= 1.0 or not 0.0 <= max_false_pass <= 1.0:
        raise ValueError("calibration thresholds must be in [0, 1]")
    pairs = list(cases)
    if any(type(expected) is not bool or type(predicted) is not bool for expected, predicted in pairs):
        raise ValueError("calibration outcomes must be booleans")
    count = len(pairs)
    if count == 0:
        raise ValueError("at least one independently adjudicated case is required")
    correct = sum(expected == predicted for expected, predicted in pairs)
    actual_failures = sum(not expected for expected, _ in pairs)
    actual_successes = sum(expected for expected, _ in pairs)
    false_passes = sum(not expected and predicted for expected, predicted in pairs)
    false_fails = sum(expected and not predicted for expected, predicted in pairs)
    accuracy = correct / count
    false_pass_rate = false_passes / actual_failures if actual_failures else None
    false_fail_rate = false_fails / actual_successes if actual_successes else None
    accepted = count >= minimum_samples and accuracy >= min_accuracy and (
        false_pass_rate is not None and false_pass_rate <= max_false_pass
    )
    if count < minimum_samples:
        reason = "insufficient-independent-cases"
    elif false_pass_rate is None:
        reason = "no-negative-cases-to-measure-false-passes"
    elif accuracy < min_accuracy:
        reason = "accuracy-below-threshold"
    elif false_pass_rate > max_false_pass:
        reason = "false-pass-rate-above-threshold"
    else:
        reason = "calibration-thresholds-passed"
    return EvaluatorCalibration(evaluator_revision, count, accuracy, false_pass_rate,
                                false_fail_rate, accepted, reason)



@dataclass(frozen=True)
class GraphLearningPolicy:
    """Pinned, immutable safety gates for graph-learning comparisons."""

    minimum_quality: float = 0.85
    maximum_quality_drop: float = 0.02
    maximum_regressions: int = 0
    maximum_cost_increase_fraction: float = 0.10
    maximum_latency_increase_fraction: float = 0.10
    minimum_holdout_tasks: int = 2

    def __post_init__(self) -> None:
        for name in ("minimum_quality", "maximum_quality_drop", "maximum_cost_increase_fraction", "maximum_latency_increase_fraction"):
            value = _finite(getattr(self, name), name)
            if value < 0 or (name in {"minimum_quality", "maximum_quality_drop"} and value > 1):
                raise ValueError(f"invalid graph learning policy value: {name}")
        if type(self.maximum_regressions) is not int or self.maximum_regressions < 0:
            raise ValueError("maximum_regressions must be a non-negative integer")
        if type(self.minimum_holdout_tasks) is not int or self.minimum_holdout_tasks < 1:
            raise ValueError("minimum_holdout_tasks must be a positive integer")


class GraphLearningEvidenceGate:
    """Record a pinned-baseline graph comparison without granting promotion authority."""

    _CANDIDATE_KEYS = frozenset({
        "baseline_id", "baseline_digest", "task_ids", "training_task_ids",
        "holdout_task_ids", "task_set_digest", "graph_topology", "concurrency",
        "model_provider", "worker_selection", "metrics", "task_digest",
        "intent_digest", "artifact_digest", "source_revision",
    })
    _FORBIDDEN_KEYS = frozenset({
        "policy", "authority", "authority_policy", "verification_thresholds",
        "rollback_requirements", "promotion_authority", "safety_thresholds",
        "minimum_quality", "maximum_regressions", "evaluator_policy",
    })
    _SHA256 = re.compile(r"^[0-9a-f]{64}$")
    _REVISION = re.compile(r"^[0-9a-f]{40,64}$")

    def __init__(
        self,
        journal: ImprovementJournal,
        *,
        pinned_baseline_id: str,
        pinned_baseline_digest: str,
        trusted_verifier_ids: Iterable[str],
        policy: GraphLearningPolicy = GraphLearningPolicy(),
    ) -> None:
        if not isinstance(journal, ImprovementJournal):
            raise TypeError("graph learning must use the canonical ImprovementJournal")
        if not isinstance(pinned_baseline_id, str) or not pinned_baseline_id.strip():
            raise ValueError("pinned_baseline_id is required")
        if not isinstance(pinned_baseline_digest, str) or not self._SHA256.fullmatch(pinned_baseline_digest):
            raise ValueError("pinned_baseline_digest must be a SHA-256 digest")
        trusted = {item.strip() for item in trusted_verifier_ids if isinstance(item, str) and item.strip()}
        if not trusted:
            raise ValueError("at least one trusted verifier identity is required")
        if not isinstance(policy, GraphLearningPolicy):
            raise TypeError("policy must be the immutable GraphLearningPolicy")
        self.journal = journal
        self.pinned_baseline_id = pinned_baseline_id.strip()
        self.pinned_baseline_digest = pinned_baseline_digest
        self.trusted_verifier_ids = frozenset(trusted)
        self.policy = policy
        self.policy_digest = _digest(asdict(policy))

    @staticmethod
    def _task_ids(record: Mapping[str, Any], field: str) -> tuple[str, ...]:
        values = record.get(field)
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError(f"{field} must be a non-empty task-id list")
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError(f"{field} contains an invalid task id")
        normalized = tuple(sorted(item.strip() for item in values))
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"{field} contains duplicate task ids")
        return normalized

    @staticmethod
    def _metrics(record: Mapping[str, Any], task_ids: tuple[str, ...], label: str) -> dict[str, dict[str, Any]]:
        metrics = record.get("metrics")
        if not isinstance(metrics, Mapping) or set(metrics) != set(task_ids):
            raise ValueError(f"{label} metrics must cover the exact pinned task set")
        normalized: dict[str, dict[str, Any]] = {}
        for task_id in task_ids:
            item = metrics[task_id]
            if not isinstance(item, Mapping):
                raise TypeError(f"{label} metric for {task_id} must be a mapping")
            latency = _finite(item.get("latency_ms"), f"{label}.{task_id}.latency_ms")
            cost = _finite(item.get("cost_units"), f"{label}.{task_id}.cost_units")
            quality = _finite(item.get("quality_score"), f"{label}.{task_id}.quality_score")
            regressions = item.get("regression_count")
            if latency < 0 or cost < 0 or not 0 <= quality <= 1:
                raise ValueError(f"{label} metric values are outside valid ranges for {task_id}")
            if type(regressions) is not int or regressions < 0:
                raise ValueError(f"{label} regression_count must be a non-negative integer for {task_id}")
            normalized[task_id] = {
                "latency_ms": latency, "cost_units": cost,
                "quality_score": quality, "regression_count": regressions,
            }
        return normalized

    @staticmethod
    def _aggregate(metrics: Mapping[str, Mapping[str, Any]], task_ids: tuple[str, ...]) -> dict[str, float | int]:
        count = len(task_ids)
        return {
            "latency_ms_mean": sum(metrics[item]["latency_ms"] for item in task_ids) / count,
            "cost_units_mean": sum(metrics[item]["cost_units"] for item in task_ids) / count,
            "quality_score_mean": sum(metrics[item]["quality_score"] for item in task_ids) / count,
            "regression_count_total": sum(metrics[item]["regression_count"] for item in task_ids),
        }

    def evaluate(
        self,
        *,
        run_id: str,
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
        verification_receipt: Mapping[str, Any],
        holdout_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            raise TypeError("baseline and candidate must be mappings")
        forbidden = self._FORBIDDEN_KEYS.intersection(candidate)
        if forbidden:
            raise PermissionError("learning candidate cannot change safety or authority controls: " + ", ".join(sorted(forbidden)))
        if set(candidate) - self._CANDIDATE_KEYS:
            raise ValueError("candidate contains fields outside the graph-learning evidence schema")
        baseline_payload = {key: value for key, value in baseline.items() if key != "baseline_digest"}
        actual_baseline_digest = _digest(baseline_payload)
        if baseline.get("baseline_id") != self.pinned_baseline_id or actual_baseline_digest != self.pinned_baseline_digest:
            raise ValueError("baseline does not match the pinned baseline identity and digest")
        if candidate.get("baseline_id") != self.pinned_baseline_id or candidate.get("baseline_digest") != self.pinned_baseline_digest:
            raise ValueError("candidate is not compared against the pinned baseline")
        baseline_task_ids = self._task_ids(baseline, "task_ids")
        candidate_task_ids = self._task_ids(candidate, "task_ids")
        baseline_training = self._task_ids(baseline, "training_task_ids")
        candidate_training = self._task_ids(candidate, "training_task_ids")
        baseline_holdouts = self._task_ids(baseline, "holdout_task_ids")
        candidate_holdouts = self._task_ids(candidate, "holdout_task_ids")
        if baseline_task_ids != candidate_task_ids or baseline_training != candidate_training or baseline_holdouts != candidate_holdouts:
            raise ValueError("baseline and candidate must use the same exact task and holdout sets")
        if set(baseline_training).intersection(baseline_holdouts) or set(baseline_training).union(baseline_holdouts) != set(baseline_task_ids):
            raise ValueError("training and holdout tasks must be disjoint and cover the pinned task set")
        if len(baseline_holdouts) < self.policy.minimum_holdout_tasks:
            raise ValueError("insufficient pinned holdout tasks")
        task_set_digest = _digest(list(baseline_task_ids))
        if baseline.get("task_set_digest") != task_set_digest or candidate.get("task_set_digest") != task_set_digest:
            raise ValueError("task set digest does not match the pinned task set")
        for record, label in ((baseline, "baseline"), (candidate, "candidate")):
            topology = record.get("graph_topology")
            provider = record.get("model_provider")
            concurrency = record.get("concurrency")
            if not isinstance(topology, Mapping) or not topology:
                raise ValueError(f"{label} graph_topology is required")
            if not isinstance(provider, Mapping) or not provider:
                raise ValueError(f"{label} model_provider is required for evidence")
            if type(concurrency) is not int or not 1 <= concurrency <= 16:
                raise ValueError(f"{label} concurrency must be between 1 and 16")
        worker_selection = candidate.get("worker_selection")
        if not isinstance(worker_selection, (Mapping, list, tuple)) or not worker_selection:
            raise ValueError("candidate worker_selection is required")
        for field in ("task_digest", "intent_digest", "artifact_digest"):
            if not isinstance(candidate.get(field), str) or not self._SHA256.fullmatch(candidate[field]):
                raise ValueError(f"candidate {field} must be a SHA-256 digest")
        if not isinstance(candidate.get("source_revision"), str) or not self._REVISION.fullmatch(candidate["source_revision"]):
            raise ValueError("candidate source_revision must be a full commit revision")

        if not isinstance(verification_receipt, Mapping):
            raise TypeError("verification_receipt must be a verifier-produced receipt")
        receipt_digest = verification_receipt.get("receipt_digest")
        receipt_unsigned = {key: value for key, value in verification_receipt.items() if key != "receipt_digest"}
        if not isinstance(receipt_digest, str) or not self._SHA256.fullmatch(receipt_digest) or _digest(receipt_unsigned) != receipt_digest:
            raise ValueError("verification receipt digest is missing or invalid")
        if verification_receipt.get("status") != "passed":
            raise PermissionError("failed or unknown verification cannot feed graph learning")
        verifier_identity = verification_receipt.get("verifier_identity")
        if verifier_identity not in self.trusted_verifier_ids or verifier_identity == verification_receipt.get("worker_identity"):
            raise PermissionError("verification receipt lacks a trusted verifier distinct from the worker")
        for field in ("source_revision", "task_digest", "intent_digest", "artifact_digest"):
            if verification_receipt.get(field) != candidate.get(field):
                raise ValueError(f"verification receipt binding mismatch for {field}")
        checks = verification_receipt.get("checks")
        if not isinstance(checks, (list, tuple)) or not checks or any(not isinstance(item, Mapping) or item.get("status") != "passed" for item in checks):
            raise PermissionError("all required verification checks must have executed and passed")

        baseline_metrics = self._metrics(baseline, baseline_task_ids, "baseline")
        candidate_metrics = self._metrics(candidate, candidate_task_ids, "candidate")
        candidate_digest = _digest(dict(candidate))
        holdout_evidence = dict(holdout_evidence) if isinstance(holdout_evidence, Mapping) else {}
        if set(holdout_evidence) != set(baseline_holdouts):
            raise ValueError("holdout evidence must cover the exact pinned holdout task set")
        for task_id, evidence in holdout_evidence.items():
            if not isinstance(evidence, Mapping) or evidence.get("scored") is not True:
                raise PermissionError(f"holdout task {task_id} lacks an executed score")
            baseline_ref, candidate_ref = evidence.get("baseline_ref"), evidence.get("candidate_ref")
            if not isinstance(baseline_ref, str) or not baseline_ref.strip() or not isinstance(candidate_ref, str) or not candidate_ref.strip():
                raise ValueError(f"holdout task {task_id} lacks baseline/candidate evidence references")
            if evidence.get("verifier_identity") not in self.trusted_verifier_ids:
                raise PermissionError(f"holdout task {task_id} has an untrusted evaluator")
            if evidence.get("verification_receipt_digest") != receipt_digest:
                raise ValueError(f"holdout task {task_id} is not bound to the verification receipt")
            baseline_metric_digest = _digest(baseline_metrics[task_id])
            candidate_metric_digest = _digest(candidate_metrics[task_id])
            score_receipt = evidence.get("score_receipt")
            score_receipt_digest = evidence.get("score_receipt_digest")
            if not isinstance(score_receipt, Mapping) or not isinstance(score_receipt_digest, str) or not self._SHA256.fullmatch(score_receipt_digest):
                raise ValueError(f"holdout task {task_id} lacks a digest-bound score receipt")
            if _digest(dict(score_receipt)) != score_receipt_digest:
                raise ValueError(f"holdout task {task_id} score receipt digest mismatch")
            expected_score_fields = {
                "task_id": task_id,
                "baseline_ref": baseline_ref,
                "candidate_ref": candidate_ref,
                "scored": True,
                "verifier_identity": evidence.get("verifier_identity"),
                "verification_receipt_digest": receipt_digest,
                "source_revision": candidate["source_revision"],
                "task_digest": candidate["task_digest"],
                "intent_digest": candidate["intent_digest"],
                "baseline_digest": self.pinned_baseline_digest,
                "candidate_digest": candidate_digest,
                "baseline_metric_digest": baseline_metric_digest,
                "candidate_metric_digest": candidate_metric_digest,
            }
            for field, expected_value in expected_score_fields.items():
                if score_receipt.get(field) != expected_value:
                    raise ValueError(f"holdout task {task_id} score receipt binding mismatch for {field}")

        baseline_all = self._aggregate(baseline_metrics, baseline_task_ids)
        candidate_all = self._aggregate(candidate_metrics, candidate_task_ids)
        baseline_holdout = self._aggregate(baseline_metrics, baseline_holdouts)
        candidate_holdout = self._aggregate(candidate_metrics, candidate_holdouts)
        policy = self.policy
        gates = {
            "minimum_quality": candidate_all["quality_score_mean"] >= policy.minimum_quality,
            "quality_non_regression": candidate_all["quality_score_mean"] >= baseline_all["quality_score_mean"] - policy.maximum_quality_drop,
            "zero_regressions": candidate_all["regression_count_total"] <= policy.maximum_regressions,
            "cost_bounded": candidate_all["cost_units_mean"] <= baseline_all["cost_units_mean"] * (1 + policy.maximum_cost_increase_fraction),
            "latency_bounded": candidate_all["latency_ms_mean"] <= baseline_all["latency_ms_mean"] * (1 + policy.maximum_latency_increase_fraction),
            "holdout_quality_non_regression": candidate_holdout["quality_score_mean"] >= baseline_holdout["quality_score_mean"] - policy.maximum_quality_drop,
            "holdout_regressions_bounded": candidate_holdout["regression_count_total"] <= policy.maximum_regressions,
        }
        eligible = all(gates.values())
        result = {
            "schema": "auren-graph-learning-evidence-v1",
            "run_id": run_id.strip(),
            "status": "eligible_for_controller_review" if eligible else "blocked",
            "baseline_id": self.pinned_baseline_id,
            "baseline_digest": self.pinned_baseline_digest,
            "candidate_digest": candidate_digest,
            "task_set_digest": task_set_digest,
            "training_task_ids": list(baseline_training),
            "holdout_task_ids": list(baseline_holdouts),
            "graph_topology": dict(candidate["graph_topology"]),
            "concurrency": candidate["concurrency"],
            "model_provider": dict(candidate["model_provider"]),
            "worker_selection": candidate["worker_selection"],
            "baseline_metrics": baseline_all,
            "candidate_metrics": candidate_all,
            "baseline_holdout_metrics": baseline_holdout,
            "candidate_holdout_metrics": candidate_holdout,
            "verification_receipt_digest": receipt_digest,
            "verifier_identity": verifier_identity,
            "external_evaluator_independence": verification_receipt.get("external_evaluator_independence", "not_proven"),
            "external_evaluator_independence_proven": verification_receipt.get("external_evaluator_independence") in {"proven", "trusted_external"},
            "safety_policy_digest": self.policy_digest,
            "safety_gates": gates,
            "recommendation": ({
                "type": "graph_topology_or_worker_selection",
                "graph_topology": dict(candidate["graph_topology"]),
                "worker_selection": candidate["worker_selection"],
                "authority_change": False,
                "policy_change": False,
                "verification_threshold_change": False,
                "rollback_requirement_change": False,
                "promotion_eligible": False,
                "controller_approval_required": True,
            } if eligible else None),
            "promotion_eligible": False,
            "controller_approval_required": True,
        }
        result["evidence_digest"] = _digest(result)
        event = self.journal.append(
            event_id=f"graph-learning:{run_id.strip()}:{candidate_digest}",
            work_id=run_id.strip(), state="graph_learning_evaluated", payload=result,
        )
        result["journal_event_id"] = event.event_id
        result["journal_event_digest"] = event.digest
        return result

