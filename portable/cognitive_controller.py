"""Cognitive control-plane planning for AUREN execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Mapping, Sequence

from .cognitive_learning import BeliefContext, CognitiveLearningLoop
from .cognitive_runtime import CognitiveRuntime
from .information_planner import InformationAction
from .world_model import PredictionError, WorldPrediction


@dataclass(frozen=True)
class CognitivePlan:
    goal_id: str | None
    goal_title: str | None
    goal_priority: int | None
    belief_ids: tuple[str, ...]
    information_action_id: str | None
    information_score: float | None
    prediction: WorldPrediction | None
    self_confidence: float
    rationale: tuple[str, ...]


class CognitiveController:
    """Turn persisted cognitive state into a bounded, serializable execution plan."""

    def __init__(self, cognitive: CognitiveRuntime) -> None:
        self.cognitive = cognitive

    def plan(self, intent: str, *, capability: str | None = None,
             uncertainty: float = 0.5, information_actions: tuple[InformationAction, ...] = (),
             entity_id: str | None = None, predicate: str | None = None,
             action: str | None = None, current_value: object | None = None,
             beliefs: Sequence[BeliefContext] = (), belief_limit: int = 8,
             context: Mapping[str, object] | None = None) -> CognitivePlan:
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        if belief_limit < 1:
            raise ValueError("belief_limit must be positive")
        goals = self.cognitive.goals.ready(limit=20)
        goal = goals[0] if goals else None
        learning = CognitiveLearningLoop(self.cognitive.memory, self.cognitive.project, self_model=self.cognitive.self_model)
        candidate_beliefs = beliefs or learning.beliefs(limit=belief_limit)
        selected_beliefs = learning.select_beliefs(candidate_beliefs, limit=belief_limit)
        info = self.cognitive.information.choose(
            uncertainty=uncertainty,
            actions=information_actions,
            max_risk=1.0,
        ) if information_actions else None
        prediction = None
        if entity_id and predicate and action:
            prediction = self.cognitive.world.predict_next(
                entity_id, predicate, action, current_value=current_value,
            )
        self_confidence = self.cognitive.self_model.profile(capability).confidence if capability else 0.0
        rationale = [
            "uses durable goal state",
            "uses persisted or supplied low-confidence beliefs",
            "uses explicit uncertainty",
            "execution authority remains with orchestrator",
        ]
        if info:
            rationale.append("selected bounded information action by expected gain per cost and risk")
        if prediction:
            rationale.append("attached empirical action-conditioned world prediction")
        if capability:
            rationale.append("attached empirical self-model confidence")
        return CognitivePlan(
            goal_id=goal.goal_id if goal else None,
            goal_title=goal.title if goal else None,
            goal_priority=goal.priority if goal else None,
            belief_ids=tuple(item.belief_id for item in selected_beliefs),
            information_action_id=info.action_id if info else None,
            information_score=info.score if info else None,
            prediction=prediction,
            self_confidence=self_confidence,
            rationale=tuple(rationale),
        )

    def enrich_context(self, intent: str, *, capability: str | None = None,
                       uncertainty: float = 0.5, information_actions: tuple[InformationAction, ...] = (),
                       entity_id: str | None = None, predicate: str | None = None,
                       action: str | None = None, current_value: object | None = None,
                       beliefs: Sequence[BeliefContext] = (), belief_limit: int = 8,
                       context: Mapping[str, object] | None = None) -> dict[str, object]:
        enriched = dict(context or {})
        plan = self.plan(
            intent, capability=capability, uncertainty=uncertainty,
            information_actions=information_actions, entity_id=entity_id,
            predicate=predicate, action=action, current_value=current_value,
            beliefs=beliefs, belief_limit=belief_limit, context=context,
        )
        enriched["auren_cognitive_plan"] = {
            "goal_id": plan.goal_id,
            "goal_title": plan.goal_title,
            "goal_priority": plan.goal_priority,
            "belief_ids": list(plan.belief_ids),
            "information_action_id": plan.information_action_id,
            "information_score": plan.information_score,
            "prediction": {
                "prediction_id": plan.prediction.prediction_id,
                "entity_id": plan.prediction.entity_id,
                "predicate": plan.prediction.predicate,
                "action": plan.prediction.action,
                "from_value": plan.prediction.from_value,
                "predicted_value": plan.prediction.predicted_value,
                "confidence": plan.prediction.confidence,
                "evidence_observation_ids": list(plan.prediction.evidence_observation_ids),
            } if plan.prediction else None,
            "self_confidence": plan.self_confidence,
            "rationale": list(plan.rationale),
        }
        return enriched

    @staticmethod
    def score_prediction(prediction: WorldPrediction | None, actual_value: object, project: str) -> PredictionError | None:
        if prediction is None:
            return None
        if prediction.project != project:
            raise ValueError("prediction belongs to a different cognitive project")
        error_digest = hashlib.sha256(
            json.dumps(
                {"predicted": prediction.predicted_value, "actual": actual_value},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return PredictionError(
            prediction_id=prediction.prediction_id,
            predicted_value=prediction.predicted_value,
            actual_value=actual_value,
            absolute_match=prediction.predicted_value == actual_value,
            error_digest=error_digest,
            measured_at=datetime.now(timezone.utc).isoformat(),
        )


__all__ = ["CognitiveController", "CognitivePlan"]
