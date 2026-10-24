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
