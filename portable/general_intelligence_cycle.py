"""Closed-loop general intelligence runtime for HWS.

This module is an integration layer over AER's existing cognitive primitives.
It does not claim to make a model AGI by itself. It supplies the missing
continuous perception -> prediction -> planning -> authorized action ->
outcome -> reflection -> learning loop needed to evaluate increasingly
general autonomous behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .learning_transfer import LearningExperience
from .end_to_end_engineering_episode import EngineeringEpisodeRequest, EngineeringEpisodeResult, EndToEndEngineeringEpisode
from .world_model import Observation, PredictionError
from .world_mega_model import MegaPlan, WorldMegaModel


@dataclass(frozen=True)
class CognitiveCycleResult:
    cycle_id: str
    intent: str
    observation: Observation
    plan: MegaPlan
    action_proposal: Mapping[str, Any]
    execution_result: Any
    prediction_error: PredictionError | None
    learning: LearningExperience | None
    accepted: bool
    next_action: str
    safety_evidence: tuple[str, ...] = ()
    engineering_episode: EngineeringEpisodeResult | None = None


class GeneralIntelligenceCycle:
    """Run one bounded perception-to-learning cycle.

    The executor is injected by the authoritative orchestration layer. This
    object cannot grant permissions, choose credentials, mutate repositories,
    or bypass safety controls. It only coordinates cognition and records the
    resulting evidence.
    """

    def __init__(self, model: WorldMegaModel) -> None:
        self.model = model

    def run(
        self,
        *,
        cycle_id: str,
        intent: str,
        observation: Observation,
        executor: Callable[[Mapping[str, Any]], Any],
        capability: str | None = None,
        uncertainty: float = 0.5,
        strategy: str = "default",
        capabilities: Sequence[str] = (),
        action: str | None = None,
        actual_value: Any | None = None,
        task_family: str = "general",
        learning_detail: str = "",
        evidence_ids: Sequence[str] = (),
        verified: bool = False,
        safety_evidence: Sequence[str] = (),
    ) -> CognitiveCycleResult:
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            raise ValueError("cycle_id is required")
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        if not callable(executor):
            raise TypeError("executor must be callable")
        if not 0 <= uncertainty <= 1:
            raise ValueError("uncertainty must be between 0 and 1")

        observed = self.model.observe(observation)
        plan = self.model.plan(
            intent.strip(),
            capability=capability,
            uncertainty=uncertainty,
            strategy=strategy,
            capabilities=capabilities,
        )

        action_name = action or "execute_plan"
        proposal = {
            "cycle_id": cycle_id.strip(),
            "intent": intent.strip(),
            "action": action_name,
            "capability": capability or "unknown",
            "strategy": plan.strategy,
            "pathway": plan.pathway,
        }

        # Execution authority stays with the injected orchestrator/executor.
        execution_result = executor(proposal)

        prediction_error = None
        if actual_value is not None:
            prediction = self.model.predict(
                observed.entity_id,
                observed.predicate,
                action_name,
                current_value=observed.value,
            )
            if prediction is not None:
                prediction_error = self.model.score_prediction(prediction, actual_value)

        learning = None
        if learning_detail.strip() and evidence_ids:
            learning = LearningExperience(
                id=cycle_id.strip(),
                source_project=self.model.project,
                task_family=task_family.strip() or "general",
                capability=capability or "unknown",
                outcome="accepted" if verified else "observed",
                detail=learning_detail.strip(),
                evidence_ids=tuple(evidence_ids),
                confidence=1.0 if verified else 0.5,
                verified=verified,
            )
            self.model.record_learning(learning)

        accepted = verified and bool(evidence_ids)
        return CognitiveCycleResult(
            cycle_id.strip(),
            intent.strip(),
            observed,
            plan,
            proposal,
            execution_result,
            prediction_error,
            learning,
            accepted,
            "continue" if accepted else "verify",
            tuple(safety_evidence),
        )


__all__ = ["CognitiveCycleResult", "GeneralIntelligenceCycle"]
