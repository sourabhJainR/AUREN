"""Provider-neutral decision primitives for machine-native agent control.

The fabric borrows the useful interface idea behind System One models: when
software needs a judgment, do not force a text generator to emit prose and
then parse it. Ask typed questions and return probabilities that deterministic
policy code can consume.

This module is deliberately provider-neutral. It does not claim to implement
Jev or RLCD. A host can adapt an external decision model later, while tests and
local development can use deterministic evaluators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class ChoiceDecision:
    selected: str
    probabilities: Mapping[str, float]
    confidence: float

    def __post_init__(self) -> None:
        if not self.selected or self.selected not in self.probabilities:
            raise ValueError("selected must be one of the choices")
        _validate_distribution(self.probabilities)
        _validate_probability(self.confidence, "confidence")


@dataclass(frozen=True)
class ScoreDecision:
    level: str
    probabilities: Mapping[str, float]
    confidence: float

    def __post_init__(self) -> None:
        if not self.level or self.level not in self.probabilities:
            raise ValueError("level must be one of the score levels")
        _validate_distribution(self.probabilities)
        _validate_probability(self.confidence, "confidence")


@dataclass(frozen=True)
class NoulDecision:
    probability_true: float
    confidence: float

    def __post_init__(self) -> None:
        _validate_probability(self.probability_true, "probability_true")
        _validate_probability(self.confidence, "confidence")


@dataclass(frozen=True)
class DecisionQuestion:
    key: str
    kind: str
    options: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()
    claim: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("question key is required")
        if self.kind not in {"choice", "score", "noul"}:
            raise ValueError("kind must be choice, score, or noul")
        if self.kind == "choice" and not self.options:
            raise ValueError("choice questions require options")
        if self.kind == "score" and not self.levels:
            raise ValueError("score questions require levels")
        if self.kind == "noul" and not self.claim.strip():
            raise ValueError("noul questions require a claim")


@dataclass(frozen=True)
class DecisionRecipe:
    """Small, reviewable contract for a batch of semantic decisions.

    A recipe keeps behavior, typed questions, thresholds and optional no-match
    choices together. Thresholds are metadata for the consuming policy; they
    are not enforced by the recipe. It adds no execution or provider layer; DecisionFabric
    remains the only evaluator. The intent is to keep semantic judgment typed
    while deterministic code retains rules, calculations, execution and policy.
    """
    name: str
    behavior: str
    questions: tuple["DecisionQuestion", ...]
    thresholds: Mapping[str, float] = field(default_factory=dict)
    no_match: Mapping[str, str] = field(default_factory=dict)
    source: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.behavior.strip():
            raise ValueError("recipe name and behavior are required")
        if not self.questions:
            raise ValueError("recipe requires at least one question")
        keys = [question.key for question in self.questions]
        if len(set(keys)) != len(keys):
            raise ValueError("recipe question keys must be unique")
        for key, value in dict(self.thresholds).items():
            if not str(key).strip() or not 0.0 <= float(value) <= 1.0:
                raise ValueError("recipe thresholds must be between 0 and 1")
        questions = {question.key: question for question in self.questions}
        for key, option in dict(self.no_match).items():
            question = questions.get(key)
            if question is None or question.kind != "choice" or option not in question.options:
                raise ValueError("no_match must reference an existing choice option")

    @property
    def digest(self) -> str:
        import hashlib
        import json
        payload = {
            "name": self.name.strip(),
            "behavior": self.behavior.strip(),
            "questions": [question.__dict__ for question in self.questions],
            "thresholds": dict(sorted((str(k), float(v)) for k, v in dict(self.thresholds).items())),
            "no_match": dict(sorted((str(k), str(v)) for k, v in dict(self.no_match).items())),
            "source": self.source.strip(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class DecisionBatch:
    state_digest: str
    decisions: Mapping[str, ChoiceDecision | ScoreDecision | NoulDecision]

    def __post_init__(self) -> None:
        if not self.state_digest:
            raise ValueError("state_digest is required")


Evaluator = Callable[[Mapping[str, Any], DecisionQuestion], ChoiceDecision | ScoreDecision | NoulDecision]


def _validate_probability(value: float, label: str) -> None:
    if not isinstance(value, (int, float)) or not isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{label} must be between 0 and 1")


def _validate_distribution(values: Mapping[str, float]) -> None:
    if not values:
        raise ValueError("probability distribution cannot be empty")
    total = 0.0
    for key, value in values.items():
        if not str(key).strip():
            raise ValueError("distribution keys must be non-empty")
        _validate_probability(float(value), f"probability[{key}]")
        total += float(value)
    if abs(total - 1.0) > 1e-6:
        raise ValueError("probabilities must sum to 1")


def state_digest(state: Mapping[str, Any]) -> str:
    import hashlib
    import json
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class DecisionFabric:
    """Evaluate many typed questions over one shared state.

    The questions are independent by contract. A provider adapter may evaluate
    them in parallel; the default implementation simply invokes the evaluator
    once per question while preserving one shared state digest.
    """

    def __init__(self, evaluator: Evaluator) -> None:
        self.evaluator = evaluator

    def evaluate_recipe(self, state: Mapping[str, Any], recipe: DecisionRecipe) -> DecisionBatch:
        """Evaluate a reviewed decision recipe without adding another runtime layer."""
        if not isinstance(recipe, DecisionRecipe):
            raise TypeError("recipe must be a DecisionRecipe")
        return self.evaluate(state, recipe.questions)

    def evaluate(self, state: Mapping[str, Any], questions: Sequence[DecisionQuestion]) -> DecisionBatch:
        if not questions:
            raise ValueError("at least one decision question is required")
        digest = state_digest(state)
        results: dict[str, ChoiceDecision | ScoreDecision | NoulDecision] = {}
        for question in questions:
            if question.key in results:
                raise ValueError(f"duplicate question key: {question.key}")
            decision = self.evaluator(state, question)
            self._validate_result(question, decision)
            results[question.key] = decision
        return DecisionBatch(digest, results)

    @staticmethod
    def _validate_result(question: DecisionQuestion, decision: object) -> None:
        if question.kind == "choice":
            if not isinstance(decision, ChoiceDecision) or set(decision.probabilities) != set(question.options):
                raise ValueError(f"choice evaluator returned an incompatible result for {question.key}")
        elif question.kind == "score":
            if not isinstance(decision, ScoreDecision) or set(decision.probabilities) != set(question.levels):
                raise ValueError(f"score evaluator returned an incompatible result for {question.key}")
        elif not isinstance(decision, NoulDecision):
            raise ValueError(f"noul evaluator returned an incompatible result for {question.key}")


class EvidenceTrustLevel(int):
    """Numeric trust ordering for evidence consumed by policy decisions."""
    MODEL_CLAIM = 0
    TOOL_OBSERVATION = 1
    DETERMINISTIC_RESULT = 2
    INDEPENDENT_VERIFICATION = 3
    INDEPENDENT_REVIEW = 4
    PRODUCTION_OBSERVATION = 5


@dataclass(frozen=True)
class EvidenceAssessment:
    evidence_id: str
    trust_level: int
    snapshot: str
    verified: bool = False
    independent: bool = False

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.snapshot.strip():
            raise ValueError("evidence_id and snapshot are required")
        if not 0 <= self.trust_level <= 5:
            raise ValueError("trust_level must be between 0 and 5")
        if self.independent and self.trust_level < EvidenceTrustLevel.INDEPENDENT_VERIFICATION:
            raise ValueError("independent evidence must be independently verifiable")


@dataclass(frozen=True)
class UncertaintyAssessment:
    confidence: float
    evidence_gap: float = 0.0
    disagreement: float = 0.0
    stale_evidence: float = 0.0
    failure_risk: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("confidence", self.confidence), ("evidence_gap", self.evidence_gap),
                            ("disagreement", self.disagreement), ("stale_evidence", self.stale_evidence),
                            ("failure_risk", self.failure_risk)):
            _validate_probability(value, name)

    @property
    def score(self) -> float:
        penalty = max(self.evidence_gap, self.disagreement, self.stale_evidence, self.failure_risk)
        return round(max(0.0, min(1.0, 0.5 * (1.0 - self.confidence) + 0.5 * penalty)), 4)

    @property
    def verification_depth(self) -> str:
        if self.score >= 0.75:
            return "independent-plus-human"
        if self.score >= 0.5:
            return "independent"
        if self.score >= 0.25:
            return "deep"
        return "standard"


@dataclass(frozen=True)
class ResourceProfile:
    cpu_available: int
    memory_mb: int
    queue_depth: int = 0
    gpu_available: bool = False

    def __post_init__(self) -> None:
        if self.cpu_available < 1 or self.memory_mb < 128 or self.queue_depth < 0:
            raise ValueError("invalid resource profile")


@dataclass(frozen=True)
class ResourceRequest:
    estimated_cpu: int = 1
    estimated_memory_mb: int = 512
    estimated_seconds: float = 60.0
    requires_gpu: bool = False
    requires_isolation: bool = False

    def __post_init__(self) -> None:
        if self.estimated_cpu < 1 or self.estimated_memory_mb < 128 or self.estimated_seconds <= 0:
            raise ValueError("invalid resource request")


@dataclass(frozen=True)
class ResourceDecision:
    lane: str
    workers: int
    timeout_seconds: float
    isolation: str
    reason: str


def assess_evidence_trust(assessments: Sequence[EvidenceAssessment], *, minimum: int = EvidenceTrustLevel.DETERMINISTIC_RESULT) -> bool:
    """Return true only when every input is verified at the requested trust level."""
    if not assessments:
        return False
    if not 0 <= minimum <= 5:
        raise ValueError("minimum trust level must be between 0 and 5")
    return all(item.verified and item.trust_level >= minimum for item in assessments)


def assess_uncertainty(*, confidence: float, evidence_gap: float = 0.0,
                       disagreement: float = 0.0, stale_evidence: float = 0.0,
                       failure_risk: float = 0.0) -> UncertaintyAssessment:
    return UncertaintyAssessment(confidence, evidence_gap, disagreement, stale_evidence, failure_risk)


def route_resource(request: ResourceRequest, profile: ResourceProfile,
                   *, historical_local_success: float | None = None) -> ResourceDecision:
    if request.requires_isolation:
        return ResourceDecision("isolated", 1, max(60.0, request.estimated_seconds * 2),
                                "strong-isolation", "task requires isolation")
    if request.requires_gpu and not profile.gpu_available:
        return ResourceDecision("cloud", 1, max(60.0, request.estimated_seconds * 2),
                                "provider-isolation", "GPU required but unavailable locally")
    local = (
        request.estimated_cpu <= profile.cpu_available
        and request.estimated_memory_mb <= profile.memory_mb
        and profile.queue_depth < max(1, profile.cpu_available * 2)
        and (historical_local_success is None or historical_local_success >= 0.8)
    )
    if local:
        return ResourceDecision("local", min(profile.cpu_available, 4),
                                max(30.0, request.estimated_seconds * 1.5),
                                "bounded-local", "capacity and empirical history support local execution")
    return ResourceDecision("cloud", 1, max(60.0, request.estimated_seconds * 2),
                            "provider-isolation", "local capacity or empirical history is insufficient")


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    allowed: bool
    reason: str
    confidence: float


class DecisionPolicy:
    """Turn fuzzy judgments into deterministic, auditable actions."""

    def __init__(self, *, min_confidence: float = 0.70) -> None:
        _validate_probability(min_confidence, "min_confidence")
        self.min_confidence = float(min_confidence)

    def gate(self, *, action: str, decision: ChoiceDecision | ScoreDecision | NoulDecision, require: str | None = None) -> PolicyDecision:
        confidence = float(decision.confidence)
        if confidence < self.min_confidence:
            return PolicyDecision(action, False, "confidence below policy threshold", confidence)
        if require is not None:
            if isinstance(decision, NoulDecision):
                passed = decision.probability_true >= self.min_confidence if require == "true" else decision.probability_true < (1.0 - self.min_confidence)
            elif isinstance(decision, (ChoiceDecision, ScoreDecision)):
                passed = decision.selected == require if isinstance(decision, ChoiceDecision) else decision.level == require
            else:
                passed = False
            if not passed:
                return PolicyDecision(action, False, f"required condition '{require}' was not met", confidence)
        return PolicyDecision(action, True, "typed decision passed policy gate", confidence)


__all__ = [
    "ChoiceDecision", "DecisionBatch", "DecisionFabric", "DecisionPolicy", "DecisionQuestion", "DecisionRecipe",
    "NoulDecision", "PolicyDecision", "ScoreDecision", "state_digest",
    "EvidenceAssessment", "EvidenceTrustLevel", "UncertaintyAssessment",
    "ResourceProfile", "ResourceRequest", "ResourceDecision",
    "assess_evidence_trust", "assess_uncertainty", "route_resource",
]
