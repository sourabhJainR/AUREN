"""Calibrated, cost-aware and shadow-only decision lifecycle primitives.

These components make routing observable and testable without granting model
outputs execution authority. Production integration must explicitly opt in;
shadow evaluation never changes the selected production decision.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Any, Callable, Mapping, Protocol, Sequence

from .decision_fabric import (
    DecisionBatch,
    DecisionFabric,
    DecisionQuestion,
    DecisionRecipe,
    state_digest,
)


def _probability(value: float, name: str) -> float:
    number = float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")
    return number


@dataclass(frozen=True)
class DecisionEvidenceEvent:
    event_id: str
    kind: str
    policy_version: str
    state_digest: str
    payload: Mapping[str, Any]


class DecisionEvidenceSink(Protocol):
    """Adapter contract for AUREN's durable canonical evidence/memory store."""
    def append(self, event: DecisionEvidenceEvent) -> None:
        """Persist an immutable, provenance-bearing decision event."""
        ...


def _evidence_event(kind: str, policy_version: str, state: Mapping[str, Any],
                    payload: Mapping[str, Any]) -> DecisionEvidenceEvent:
    import hashlib
    import json
    digest = state_digest(state)
    canonical = json.dumps(
        {"kind": kind, "policy_version": policy_version, "state_digest": digest,
         "payload": dict(payload)},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    event_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DecisionEvidenceEvent(event_id, kind, policy_version, digest, dict(payload))


@dataclass(frozen=True)
class CalibrationObservation:
    """A confidence and its binary correctness outcome from a completed task."""
    confidence: float
    correct: bool

    def __post_init__(self) -> None:
        _probability(self.confidence, "confidence")
        if not isinstance(self.correct, bool):
            raise ValueError("correct must be boolean")


@dataclass(frozen=True)
class CalibrationReport:
    sample_count: int
    mean_confidence: float
    observed_accuracy: float
    expected_calibration_error: float
    brier_score: float


class ConfidenceCalibrator:
    """Empirical, conservative confidence correction with minimum sample gates.

    The estimate is blended toward 0.5 until enough examples exist, avoiding
    overconfident routing from tiny samples. This is an empirical correction,
    not a guarantee that future predictions are calibrated.
    """

    def __init__(self, *, bins: int = 10, min_samples: int = 20, prior_weight: float = 10.0,
                 evidence_sink: DecisionEvidenceSink | None = None, policy_version: str = "calibration-v1") -> None:
        if bins < 2 or min_samples < 1 or prior_weight < 0:
            raise ValueError("invalid calibration configuration")
        self.bins = bins
        self.min_samples = min_samples
        self.prior_weight = float(prior_weight)
        self.evidence_sink = evidence_sink
        self.policy_version = policy_version
        self._observations: list[CalibrationObservation] = []

    def add(self, observation: CalibrationObservation) -> None:
        self._observations.append(observation)
        if self.evidence_sink is not None:
            payload = {"confidence": observation.confidence, "correct": observation.correct,
                       "sample_index": len(self._observations)}
            event = _evidence_event("calibration-outcome", self.policy_version, payload, payload)
            self.evidence_sink.append(event)

    def extend(self, observations: Sequence[CalibrationObservation]) -> None:
        for observation in observations:
            self.add(observation)

    @property
    def sample_count(self) -> int:
        return len(self._observations)

    def calibrate(self, confidence: float) -> float:
        confidence = _probability(confidence, "confidence")
        if len(self._observations) < self.min_samples:
            # Shrink toward an uninformative 0.5 prior until the minimum sample
            # gate is met; the old expression accidentally returned confidence
            # unchanged because its upper bound was always >= confidence.
            sample_fraction = len(self._observations) / self.min_samples
            return round(min(confidence, 0.5 + (confidence - 0.5) * sample_fraction), 4)
        index = min(self.bins - 1, int(confidence * self.bins))
        bucket = [item for item in self._observations
                  if min(self.bins - 1, int(item.confidence * self.bins)) == index]
        minimum_bucket = max(3, self.min_samples // self.bins)
        if len(bucket) < minimum_bucket:
            bucket_fraction = min(1.0, len(bucket) / minimum_bucket)
            return round(min(confidence, 0.5 + (confidence - 0.5) * bucket_fraction), 4)
        accuracy = sum(item.correct for item in bucket) / len(bucket)
        blended = (accuracy * len(bucket) + 0.5 * self.prior_weight) / (len(bucket) + self.prior_weight)
        # Never use calibration to inflate the original confidence.
        return round(max(0.0, min(confidence, blended)), 4)

    def report(self) -> CalibrationReport:
        count = len(self._observations)
        if not count:
            return CalibrationReport(0, 0.0, 0.0, 0.0, 0.0)
        mean_confidence = sum(x.confidence for x in self._observations) / count
        accuracy = sum(x.correct for x in self._observations) / count
        ece = 0.0
        for index in range(self.bins):
            bucket = [x for x in self._observations
                      if min(self.bins - 1, int(x.confidence * self.bins)) == index]
            if bucket:
                avg_conf = sum(x.confidence for x in bucket) / len(bucket)
                avg_acc = sum(x.correct for x in bucket) / len(bucket)
                ece += len(bucket) / count * abs(avg_conf - avg_acc)
        brier = sum((x.confidence - float(x.correct)) ** 2 for x in self._observations) / count
        return CalibrationReport(count, mean_confidence, accuracy, ece, brier)


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    lane: str
    estimated_cost: float
    estimated_latency_ms: int
    historical_success: float
    supports_private_data: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or self.lane not in {"deterministic", "local", "external", "human"}:
            raise ValueError("invalid model candidate")
        if self.estimated_cost < 0 or self.estimated_latency_ms < 0:
            raise ValueError("cost and latency must be non-negative")
        _probability(self.historical_success, "historical_success")


@dataclass(frozen=True)
class CascadeRequest:
    complexity: float
    risk: float
    privacy_required: bool
    latency_budget_ms: int
    cost_budget: float
    minimum_success: float = 0.8
    irreversible_action: bool = False

    def __post_init__(self) -> None:
        _probability(self.complexity, "complexity")
        _probability(self.risk, "risk")
        _probability(self.minimum_success, "minimum_success")
        if self.latency_budget_ms < 0 or self.cost_budget < 0:
            raise ValueError("budgets must be non-negative")


@dataclass(frozen=True)
class CascadeDecision:
    candidate: ModelCandidate
    reason: str
    requires_human_approval: bool


class CostAwareCascade:
    """Choose a feasible route; risk/authority gates dominate cost optimization."""

    def __init__(self, candidates: Sequence[ModelCandidate]) -> None:
        if not candidates:
            raise ValueError("at least one model candidate is required")
        self.candidates = tuple(candidates)

    def choose(self, request: CascadeRequest) -> CascadeDecision:
        if request.irreversible_action or request.risk >= 0.9:
            human = next((c for c in self.candidates if c.lane == "human"), None)
            if human is None:
                raise RuntimeError("human approval required but no human route is configured")
            return CascadeDecision(human, "authority/risk gate precedes cost routing", True)

        feasible = [
            c for c in self.candidates
            if c.lane != "human"
            and c.estimated_cost <= request.cost_budget
            and c.estimated_latency_ms <= request.latency_budget_ms
            and c.historical_success >= request.minimum_success
            and (c.lane != "deterministic" or request.complexity <= 0.25)
            and (not request.privacy_required or c.lane in {"deterministic", "local"} or c.supports_private_data)
        ]
        if not feasible:
            human = next((c for c in self.candidates if c.lane == "human"), None)
            if human is None:
                raise RuntimeError("no candidate satisfies budgets and privacy policy")
            return CascadeDecision(human, "no feasible route; escalate instead of unsafe fallback", True)

        # Deterministic first when it meets the task's complexity; otherwise
        # prefer quality-feasible local, then external, while respecting cost.
        deterministic = [c for c in feasible if c.lane == "deterministic"]
        if deterministic and request.complexity <= 0.25:
            selected = min(deterministic, key=lambda c: (c.estimated_cost, c.estimated_latency_ms))
        else:
            selected = min(feasible, key=lambda c: (
                c.estimated_cost,
                0 if c.lane == "local" else 1 if c.lane == "external" else 2,
                c.estimated_latency_ms,
            ))
        return CascadeDecision(selected, "lowest-cost feasible candidate under quality, latency and privacy constraints", False)


class BatchDecisionProvider(Protocol):
    def evaluate_batch(self, state: Mapping[str, Any], questions: Sequence[DecisionQuestion]) -> Mapping[str, Any]:
        """Return one typed result for each question key."""
        ...


def evaluate_batch(
    state: Mapping[str, Any],
    questions: Sequence[DecisionQuestion],
    provider: BatchDecisionProvider,
) -> DecisionBatch:
    """Evaluate a typed batch against one immutable state snapshot.

    The adapter must return typed decision objects. DecisionFabric validates
    every answer and rejects missing, extra-incompatible, or malformed values.
    """
    if not questions:
        raise ValueError("at least one decision question is required")
    snapshot = dict(state)
    digest = state_digest(snapshot)
    answers = provider.evaluate_batch(snapshot, questions)
    if not isinstance(answers, Mapping):
        raise ValueError("batch provider must return a mapping")
    expected = {q.key for q in questions}
    if set(answers) != expected:
        raise ValueError("batch provider response keys must exactly match question keys")
    result = DecisionFabric(lambda _state, question: answers[question.key]).evaluate(snapshot, questions)
    if result.state_digest != digest:
        raise RuntimeError("state snapshot digest changed during batch evaluation")
    return result


@dataclass(frozen=True)
class ShadowRecord:
    state_digest: str
    baseline: str
    candidate: str
    candidate_would_change: bool
    outcome_correct: bool | None
    policy_version: str


class ShadowEvaluator:
    """Record candidate policy results without altering baseline execution."""

    def __init__(self, *, policy_version: str, evidence_sink: DecisionEvidenceSink | None = None) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version is required")
        self.policy_version = policy_version
        self.evidence_sink = evidence_sink
        self._records: list[ShadowRecord] = []

    def compare(
        self,
        state: Mapping[str, Any],
        *,
        baseline: str,
        candidate: str,
        outcome_correct: bool | None = None,
    ) -> ShadowRecord:
        if not baseline.strip() or not candidate.strip():
            raise ValueError("baseline and candidate decisions are required")
        record = ShadowRecord(
            state_digest(state), baseline, candidate, baseline != candidate,
            outcome_correct, self.policy_version,
        )
        self._records.append(record)
        if self.evidence_sink is not None:
            payload = {"baseline": baseline, "candidate": candidate,
                       "candidate_would_change": record.candidate_would_change,
                       "outcome_correct": outcome_correct}
            event = _evidence_event("shadow-comparison", self.policy_version, state, payload)
            self.evidence_sink.append(event)
        return record

    def observe_outcome(self, *, state_digest_value: str, outcome_correct: bool) -> ShadowRecord:
        """Attach an observed result to the matching shadow decision receipt."""
        if not state_digest_value.strip() or not isinstance(outcome_correct, bool):
            raise ValueError("state digest and boolean outcome are required")
        for index in range(len(self._records) - 1, -1, -1):
            record = self._records[index]
            if record.state_digest == state_digest_value and record.outcome_correct is None:
                updated = replace(record, outcome_correct=outcome_correct)
                self._records[index] = updated
                if self.evidence_sink is not None:
                    payload = {
                        "source_state_digest": state_digest_value,
                        "baseline": record.baseline,
                        "candidate": record.candidate,
                        "outcome_correct": outcome_correct,
                    }
                    event = _evidence_event(
                        "shadow-outcome", self.policy_version,
                        {"source_state_digest": state_digest_value}, payload,
                    )
                    self.evidence_sink.append(event)
                return updated
        raise KeyError("no pending shadow record matches the supplied state digest")

    @property
    def records(self) -> tuple[ShadowRecord, ...]:
        return tuple(self._records)

    def promotion_eligible(
        self,
        *,
        minimum_samples: int = 100,
        minimum_accuracy: float = 0.95,
        maximum_regression_rate: float = 0.01,
    ) -> bool:
        _probability(minimum_accuracy, "minimum_accuracy")
        _probability(maximum_regression_rate, "maximum_regression_rate")
        observed = [r for r in self._records if r.outcome_correct is not None]
        if len(observed) < minimum_samples:
            return False
        accuracy = sum(bool(r.outcome_correct) for r in observed) / len(observed)
        changed = [r for r in observed if r.candidate_would_change]
        regressions = sum(not bool(r.outcome_correct) for r in changed)
        regression_rate = regressions / len(changed) if changed else 0.0
        return accuracy >= minimum_accuracy and regression_rate <= maximum_regression_rate


@dataclass(frozen=True)
class RoutingResult:
    inference_depth: str
    calibrated_confidence: float
    inference_provider: str
    selected_model: ModelCandidate
    candidate_model: ModelCandidate
    reason: str
    requires_human_approval: bool
    shadow_record: ShadowRecord | None = None


class DecisionRouter:
    """Compose calibration, inference-depth safety, model cascade and shadow logging.

    The router selects a lane only. It does not execute tools, grant permissions,
    approve irreversible actions, or change the baseline because of shadow data.
    """

    def __init__(
        self,
        *,
        inference_policy: Any,
        calibrator: ConfidenceCalibrator,
        cascade: CostAwareCascade,
        shadow: ShadowEvaluator | None = None,
        minimum_calibrated_confidence: float = 0.75,
        shadow_only: bool = False,
    ) -> None:
        self.inference_policy = inference_policy
        self.calibrator = calibrator
        self.cascade = cascade
        self.shadow = shadow
        if shadow_only and shadow is None:
            raise ValueError("shadow-only mode requires a ShadowEvaluator")
        self.minimum_calibrated_confidence = _probability(
            minimum_calibrated_confidence, "minimum_calibrated_confidence"
        )
        self.shadow_only = bool(shadow_only)

    def route(
        self,
        state: Mapping[str, Any],
        *,
        uncertainty: float,
        risk: float,
        evidence_quality: float,
        failure_probability: float = 0.0,
        complexity: float,
        privacy_required: bool,
        latency_budget_ms: int,
        cost_budget: float,
        irreversible_action: bool = False,
        baseline_lane: str = "deterministic",
    ) -> RoutingResult:
        from .adaptive_decision import AdaptiveInferencePolicy

        decision = self.inference_policy.decide(
            uncertainty=uncertainty,
            risk=risk,
            evidence_quality=evidence_quality,
            failure_probability=failure_probability,
        )
        calibrated = self.calibrator.calibrate(decision.confidence)
        if decision.provider == "external" and calibrated < self.minimum_calibrated_confidence:
            # Do not accept an external recommendation when its empirically
            # corrected confidence is too low; recompute with rules only.
            deterministic = AdaptiveInferencePolicy().decide(
                uncertainty=uncertainty,
                risk=risk,
                evidence_quality=evidence_quality,
                failure_probability=failure_probability,
            )
            decision = deterministic
            calibrated = self.calibrator.calibrate(deterministic.confidence)

        cascade = self.cascade.choose(CascadeRequest(
            complexity=complexity,
            risk=risk,
            privacy_required=privacy_required,
            latency_budget_ms=latency_budget_ms,
            cost_budget=cost_budget,
            irreversible_action=irreversible_action,
        ))
        candidate = cascade.candidate
        if decision.depth == "human" and candidate.lane != "human":
            human = next((item for item in self.cascade.candidates if item.lane == "human"), None)
            if human is None:
                raise RuntimeError("human inference depth requires a configured human route")
            candidate = human

        selected = candidate
        if self.shadow_only and decision.depth != "human":
            baseline = next(
                (item for item in self.cascade.candidates if item.lane == baseline_lane), None
            )
            if baseline is None:
                raise RuntimeError("shadow-only routing requires a configured baseline lane")
            selected = baseline

        shadow_record = None
        if self.shadow is not None:
            shadow_record = self.shadow.compare(
                state, baseline=baseline_lane, candidate=candidate.lane, outcome_correct=None
            )
        reason = (
            f"shadow-only: retained baseline lane '{baseline_lane}'; {cascade.reason}"
            if self.shadow_only and decision.depth != "human" else cascade.reason
        )
        return RoutingResult(
            decision.depth, calibrated, decision.provider, selected, candidate,
            reason, cascade.requires_human_approval or decision.depth == "human",
            shadow_record,
        )


__all__ = [
    "CalibrationObservation", "CalibrationReport", "ConfidenceCalibrator", "DecisionEvidenceEvent", "DecisionEvidenceSink", "RoutingResult", "DecisionRouter",
    "ModelCandidate", "CascadeRequest", "CascadeDecision", "CostAwareCascade",
    "BatchDecisionProvider", "evaluate_batch", "ShadowRecord", "ShadowEvaluator",
]
