"""Bounded autonomous campaign -> attribution -> learning -> holdout -> rollout loop.

This controller is deliberately evaluation-only: it selects already-authorized
benchmark requests, attributes observed outcomes conservatively, persists the
learning signal, proposes a bounded intervention, and gates promotion/rollback
through the existing capability lifecycle. It never grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .benchmark_execution_handshake import BenchmarkExecutionReceipt
from .benchmark_task_dispatch import BenchmarkExecutionRequest
from .capability_lifecycle import CapabilityLifecycle, CapabilityLifecycleReceipt
from .learning_steward import LearningSteward


FAILURE_CLASSES = (
    "capability",
    "decomposition",
    "routing",
    "model",
    "resource",
    "verification",
    "safety",
    "unknown",
)


@dataclass(frozen=True)
class CampaignPrediction:
    task_id: str
    predicted_success: bool
    predicted_score: float
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("prediction requires task id")
        if not 0.0 <= float(self.predicted_score) <= 1.0:
            raise ValueError("predicted score must be within [0,1]")


@dataclass(frozen=True)
class CampaignAttribution:
    task_id: str
    predicted_success: bool
    realized_success: bool
    predicted_score: float
    realized_score: float
    score_error: float
    failure_class: str
    confidence: float
    evidence_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.failure_class not in FAILURE_CLASSES:
            raise ValueError("unsupported failure class")

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "predicted_success": self.predicted_success,
            "realized_success": self.realized_success,
            "predicted_score": round(self.predicted_score, 3),
            "realized_score": round(self.realized_score, 3),
            "score_error": round(self.score_error, 3),
            "failure_class": self.failure_class,
            "confidence": round(self.confidence, 3),
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CampaignIntervention:
    task_id: str
    failure_class: str
    action: str
    holdout_required: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "failure_class": self.failure_class,
            "action": self.action,
            "holdout_required": self.holdout_required,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CampaignLearning:
    campaign_id: str
    observations: tuple[CampaignAttribution, ...]
    interventions: tuple[CampaignIntervention, ...]
    selected_task_ids: tuple[str, ...]
    holdout_task_ids: tuple[str, ...]
    rollout: CapabilityLifecycleReceipt | None

    def as_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "observations": [x.as_dict() for x in self.observations],
            "interventions": [x.as_dict() for x in self.interventions],
            "selected_task_ids": list(self.selected_task_ids),
            "holdout_task_ids": list(self.holdout_task_ids),
            "rollout": self.rollout.__dict__ if self.rollout else None,
        }


class AutonomousCampaignController:
    """Run the decision side of a bounded closed-loop benchmark campaign."""

    def __init__(
        self,
        root: Path,
        *,
        max_tasks: int = 8,
        minimum_holdout_score: float = 0.75,
        minimum_promotion_score: float = 0.80,
        lifecycle_canaries: int = 3,
    ) -> None:
        self.root = Path(root)
        self.max_tasks = max(1, min(8, int(max_tasks)))
        self.minimum_holdout_score = max(0.0, min(1.0, float(minimum_holdout_score)))
        self.minimum_promotion_score = max(0.0, min(1.0, float(minimum_promotion_score)))
        self.lifecycle_canaries = max(1, int(lifecycle_canaries))

    def select(self, requests: Iterable[BenchmarkExecutionRequest], *, max_tasks: int | None = None) -> tuple[BenchmarkExecutionRequest, ...]:
        limit = self.max_tasks if max_tasks is None else max(1, min(self.max_tasks, int(max_tasks)))
        rows = tuple(requests)
        if len({r.task_id for r in rows}) != len(rows):
            raise ValueError("campaign requests must have unique task ids")
        # Prefer holdouts, then unseen domains, while remaining deterministic.
        return tuple(sorted(rows, key=lambda r: (not r.holdout, r.domain, r.task_id))[:limit])

    @staticmethod
    def attribute(
        request: BenchmarkExecutionRequest,
        prediction: CampaignPrediction,
        receipt: BenchmarkExecutionReceipt,
        *,
        realized_score: float,
    ) -> CampaignAttribution:
        score = max(0.0, min(1.0, float(realized_score)))
        error = abs(score - prediction.predicted_score)
        if receipt.accepted and prediction.predicted_success:
            failure = "unknown"
            reason = "prediction and verified execution agree"
            confidence = max(0.5, 1.0 - error)
        elif not receipt.accepted:
            reason_text = (receipt.reason or "").lower()
            if "verification" in reason_text:
                failure = "verification"
            elif "safety" in reason_text:
                failure = "safety"
            elif "resource" in reason_text:
                failure = "resource"
            elif "evidence" in reason_text:
                failure = "capability"
            elif prediction.predicted_success:
                failure = "capability"
            else:
                failure = "unknown"
            reason = "verified execution did not meet the predicted outcome: " + receipt.reason
            confidence = max(0.5, min(1.0, 0.5 + min(0.5, error)))
        elif not prediction.predicted_success:
            failure = "model"
            reason = "execution exceeded the negative prediction; recalibrate outcome model"
            confidence = max(0.5, 1.0 - error)
        else:
            failure = "model"
            reason = "prediction direction was correct but score was materially miscalibrated"
            confidence = max(0.5, 1.0 - error)
        return CampaignAttribution(
            request.task_id, prediction.predicted_success, receipt.accepted,
            prediction.predicted_score, score, error, failure, confidence,
            tuple(receipt.evidence_ids), reason,
        )

    @staticmethod
    def intervention(attribution: CampaignAttribution) -> CampaignIntervention | None:
        if attribution.failure_class == "unknown" and attribution.score_error < 0.10:
            return None
        actions = {
            "capability": "probe a missing capability on an independent holdout",
            "decomposition": "re-plan the task into independently verifiable subgoals",
            "routing": "re-evaluate model/tool/skill routing under the same task contract",
            "model": "recalibrate the outcome predictor against realized evidence",
            "resource": "reduce or rebalance resource allocation within the existing budget",
            "verification": "strengthen independent verification without weakening the task gate",
            "safety": "retain incumbent behavior and require a fresh safety-reviewed holdout",
            "unknown": "run a diagnostic holdout before changing execution policy",
        }
        return CampaignIntervention(
            attribution.task_id, attribution.failure_class, actions[attribution.failure_class],
            True, "intervention must be validated on an independent holdout",
        )

    def learn(self, campaign_id: str, observations: Sequence[CampaignAttribution]) -> None:
        steward = LearningSteward(self.root, run_id=campaign_id, task="autonomous benchmark campaign")
        for row in observations:
            steward.record_experience(
                key=f"team:autonomous-campaign:{row.failure_class}",
                outcome="passed" if row.realized_success else "failed",
                evidence_quality=row.realized_score,
                cost_score=1.0 - row.realized_score,
                duration_seconds=0.0,
                decision=json.dumps(row.as_dict(), sort_keys=True),
                evidence_ids=row.evidence_ids,
            )

    def run(
        self,
        campaign_id: str,
        requests: Iterable[BenchmarkExecutionRequest],
        *,
        execute: Callable[[BenchmarkExecutionRequest], tuple[BenchmarkExecutionReceipt, float]],
        predictions: Mapping[str, CampaignPrediction],
        capability_id: str | None = None,
        baseline_score: float | None = None,
    ) -> CampaignLearning:
        selected = self.select(requests)
        observations = []
        interventions = []
        for request in selected:
            prediction = predictions.get(request.task_id)
            if prediction is None:
                raise ValueError(f"missing prediction for {request.task_id}")
            receipt, realized_score = execute(request)
            observation = self.attribute(request, prediction, receipt, realized_score=realized_score)
            observations.append(observation)
            proposed = self.intervention(observation)
            if proposed:
                interventions.append(proposed)
        self.learn(campaign_id, observations)

        rollout = None
        if capability_id and baseline_score is not None:
            lifecycle = CapabilityLifecycle(
                _persistent_memory(self.root), "autonomous-campaign",
                min_canaries=self.lifecycle_canaries,
            )
            try:
                lifecycle.begin(capability_id, baseline_score=float(baseline_score))
            except ValueError:
                pass
            # Feed every holdout result into the lifecycle. A weak or unsafe
            # holdout must be visible to rollback; only passing holdouts count
            # toward promotion.
            holdout_ids = {r.task_id for r in selected if r.holdout}
            for index, observation in enumerate(
                x for x in observations if x.task_id in holdout_ids
            ):
                rollout = lifecycle.record_canary(
                    capability_id, score=observation.realized_score,
                    evidence_id=f"{campaign_id}:holdout:{index}",
                    safe=(
                        observation.failure_class != "safety"
                        and observation.realized_success
                    ),
                    metadata={
                        "campaign_id": campaign_id,
                        "holdout_score_gate": self.minimum_holdout_score,
                    },
                )
                if rollout.state == "rolled_back":
                    break
                if (
                    rollout.state == "promoted"
                    and observation.realized_score >= self.minimum_holdout_score
                ):
                    break
            if rollout is None:
                rollout = lifecycle.status(capability_id)
        return CampaignLearning(
            campaign_id, tuple(observations), tuple(interventions),
            tuple(x.task_id for x in selected),
            tuple(x.task_id for x in selected if x.holdout),
            rollout,
        )


def _persistent_memory(root: Path):
    # Keep the controller free of a hard runtime dependency; use the repository's
    # existing persistent memory implementation when capability lifecycle is asked for.
    from .persistent_memory import PersistentMemory
    return PersistentMemory(Path(root) / ".ai-harness" / "state" / "autonomous-campaign-memory.sqlite3", require_approval=False)


__all__ = [
    "AutonomousCampaignController",
    "CampaignAttribution",
    "CampaignIntervention",
    "CampaignLearning",
    "CampaignPrediction",
    "FAILURE_CLASSES",
]
