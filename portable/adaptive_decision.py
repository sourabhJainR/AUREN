"""Provider-neutral adaptive decision and inference-depth control.

The module keeps exact rules in code, bounded judgments behind typed decisions,
and external decision models optional. A fast decision provider can be plugged
in later without changing the execution graph or permission boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Mapping, Sequence
from urllib import request

from .decision_fabric import (
    ChoiceDecision,
    DecisionFabric,
    DecisionQuestion,
    DecisionRecipe,
    NoulDecision,
    ScoreDecision,
)


DecisionEvaluator = Callable[[Mapping[str, Any], DecisionQuestion], Any]


INFERENCE_DEPTH_RECIPE = DecisionRecipe(
    name="inference-depth",
    behavior="Choose the minimum safe inference depth; escalate rather than invent certainty.",
    questions=(DecisionQuestion(
        "depth",
        "score",
        levels=("minimal", "standard", "deep", "human"),
        claim="Choose the minimum safe inference depth for this execution decision.",
    ),),
    thresholds={"provider_confidence": 0.75},
    source="AUREN typed decision contract",
)


@dataclass(frozen=True)
class InferenceDecision:
    depth: str
    confidence: float
    reason: str
    provider: str = "deterministic"
    abstained: bool = False

    def __post_init__(self) -> None:
        if self.depth not in {"minimal", "standard", "deep", "human"}:
            raise ValueError("unsupported inference depth")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


class HttpDecisionProvider:
    """Optional typed-decision HTTP adapter.

    The endpoint is deliberately generic so a Jev/System-One compatible
    service can be supplied without making the runtime depend on that service.
    The adapter is never called unless explicitly configured.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 2.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.endpoint = endpoint or os.getenv("DECISION_MODEL_ENDPOINT", "")
        self.model = model or os.getenv("DECISION_MODEL_NAME", "decision-latest")
        self.api_key = api_key or os.getenv("DECISION_MODEL_API_KEY")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._opener = opener or request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.endpoint.strip())

    def evaluate(self, state: Mapping[str, Any], questions: Sequence[DecisionQuestion]):
        if not self.configured:
            raise RuntimeError("typed decision provider is not configured")
        payload_questions: dict[str, dict[str, Any]] = {}
        for question in questions:
            payload_questions[question.key] = {
                "type": question.kind,
                "instructions": question.claim or question.key,
                "options": list(question.options),
                "levels": list(question.levels),
            }
        body = json.dumps(
            {"state": state, "model": self.model, "questions": payload_questions},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self._opener(
            request.Request(self.endpoint, data=body, headers=headers, method="POST"),
            timeout=self.timeout_seconds,
        )
        with response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("typed decision provider returned a non-object")
        answers = data.get("decisions", data)
        if not isinstance(answers, Mapping):
            raise ValueError("typed decision provider returned invalid decisions")
        return {
            key: self._decode(question, answers[key])
            for key, question in ((q.key, q) for q in questions)
        }

    @staticmethod
    def _decode(question: DecisionQuestion, value: Any):
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid decision for {question.key}")
        confidence = float(value.get("confidence", 0.0))
        probabilities = value.get("probabilities", {})
        if question.kind == "choice":
            return ChoiceDecision(str(value["choice"]), {str(k): float(v) for k, v in probabilities.items()}, confidence)
        if question.kind == "score":
            return ScoreDecision(str(value.get("level")), {str(k): float(v) for k, v in probabilities.items()}, confidence)
        return NoulDecision(float(value.get("probability_true", value.get("probability", 0.0))), confidence)


class AdaptiveInferencePolicy:
    """Choose reasoning depth from uncertainty, risk and learned failure evidence."""

    def __init__(
        self,
        *,
        provider: HttpDecisionProvider | None = None,
        min_confidence: float | None = None,
    ) -> None:
        self.provider = provider
        configured_confidence = INFERENCE_DEPTH_RECIPE.thresholds["provider_confidence"] if min_confidence is None else float(min_confidence)
        self.min_confidence = max(0.5, min(0.99, configured_confidence))

    def decide(
        self,
        *,
        uncertainty: float,
        risk: float,
        evidence_quality: float,
        failure_probability: float = 0.0,
    ) -> InferenceDecision:
        values = {
            "uncertainty": max(0.0, min(1.0, float(uncertainty))),
            "risk": max(0.0, min(1.0, float(risk))),
            "evidence_quality": max(0.0, min(1.0, float(evidence_quality))),
            "failure_probability": max(0.0, min(1.0, float(failure_probability))),
        }
        state = dict(values)
        question = INFERENCE_DEPTH_RECIPE.questions[0]
        evaluator = self.provider
        if evaluator and evaluator.configured:
            try:
                batch = DecisionFabric(lambda current, item: evaluator.evaluate(current, (item,))[item.key]).evaluate_recipe(state, INFERENCE_DEPTH_RECIPE)
                decision = batch.decisions["depth"]
                if isinstance(decision, ScoreDecision) and decision.confidence >= self.min_confidence:
                    return InferenceDecision(
                        decision.level,
                        decision.confidence,
                        "typed provider decision passed confidence gate",
                        "external",
                    )
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, json.JSONDecodeError):
                pass

        pressure = max(
            values["uncertainty"],
            values["risk"],
            values["failure_probability"],
            1.0 - values["evidence_quality"],
        )
        if values["risk"] >= 0.9 or values["failure_probability"] >= 0.75:
            depth = "human"
        elif pressure >= 0.72:
            depth = "deep"
        elif pressure >= 0.38:
            depth = "standard"
        else:
            depth = "minimal"
        confidence = max(0.0, min(1.0, 1.0 - pressure * 0.6))
        return InferenceDecision(
            depth,
            confidence,
            "deterministic safety policy selected the minimum sufficient depth",
        )


__all__ = ["AdaptiveInferencePolicy", "HttpDecisionProvider", "InferenceDecision", "INFERENCE_DEPTH_RECIPE"]
