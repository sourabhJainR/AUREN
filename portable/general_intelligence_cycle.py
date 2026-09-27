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


    def run_engineering_episode(
        self,
        *,
        cycle_id: str,
        intent: str,
        observation: Observation,
        request: EngineeringEpisodeRequest,
        executor: Callable[[EngineeringEpisodeRequest], Any],
        episode: EndToEndEngineeringEpisode | None = None,
        **episode_kwargs: Any,
    ) -> CognitiveCycleResult:
        """Run an engineering episode and feed verified outcomes into learning."""
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            raise ValueError("cycle_id is required")
        if not isinstance(request, EngineeringEpisodeRequest):
            raise TypeError("request must be an EngineeringEpisodeRequest")
        if not callable(executor):
            raise TypeError("executor must be callable")

        observed = self.model.observe(observation)
        plan = self.model.plan(intent.strip(), uncertainty=0.5)
        engineering = episode or EndToEndEngineeringEpisode()
        result = engineering.run(request, executor=executor, **episode_kwargs)
        evidence = tuple(dict.fromkeys(result.evidence_ids))

        learning = None
        if result.accepted and evidence:
            learning = LearningExperience(
                id=f"{cycle_id.strip()}:engineering",
                source_project=self.model.project,
                task_family="software-engineering",
                capability="end-to-end-engineering",
                outcome="worked",
                detail=(
                    f"Verified engineering episode: {request.intent}. "
                    f"coverage={result.engineering.completeness_ratio:.3f}; "
                    f"score={result.engineering.overall_score:.3f}"
                ),
                evidence_ids=evidence,
                confidence=min(1.0, max(0.5, result.engineering.overall_score)),
                verified=True,
            )
            self.model.record_learning(learning)

        if not result.accepted:
            self.model.record_failure_dont(
                problem=request.intent,
                dont="; ".join(result.defects) or result.next_action,
                evidence_ids=evidence,
                confidence=0.95,
            )
        return CognitiveCycleResult(
            cycle_id.strip(),
            intent.strip(),
            observed,
            plan,
            {"cycle_id": cycle_id.strip(), "action": "engineering_episode", "avoid": tuple(x.dont for x in plan.avoid)},
            result.implementation_result,
            None,
            learning,
            result.accepted,
            "continue" if result.accepted else result.next_action,
            evidence,
            result,
        )


__all__ = ["CognitiveCycleResult", "GeneralIntelligenceCycle"]
