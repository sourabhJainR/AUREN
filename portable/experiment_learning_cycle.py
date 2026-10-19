"""End-to-end persistence bridge for adaptive experiment outcomes.

The cycle aggregates only already-verified independent replications, derives the
curriculum signal, and persists its manifest/result/signal/evidence lineage before
returning. It deliberately does not promote candidates or mutate an active harness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from .adaptive_experiment_controller import (
    AdaptiveExperimentResult,
    ExperimentController,
    ExperimentReplication,
)
from .engineering_episode import EngineeringEpisode
from .experiment_learning_bridge import CurriculumLearningSignal, ExperimentLearningBridge
from .external_evaluation_campaign import CampaignRetestContract, LearningIntervention
from .persistent_experiment_learning import (
    ExperimentManifest,
    PersistentExperimentLearningStore,
    PersistentLearningReceipt,
    LearningStoreConflict,
)


@dataclass(frozen=True, slots=True)
class ExperimentLearningCycleResult:
    experiment_result: AdaptiveExperimentResult
    signal: CurriculumLearningSignal
    receipt: PersistentLearningReceipt

    @property
    def cycle_digest(self) -> str:
        payload = {
            "result_digest": self.experiment_result.result_digest,
            "signal_digest": self.signal.signal_digest,
            "receipt_id": self.receipt.receipt_id,
            "receipt_digest": self.receipt.record_digest,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()


class ExperimentLearningCycle:
    """Join adaptive aggregation to durable, evidence-gated learning storage."""

    def __init__(
        self,
        *,
        controller: ExperimentController,
        learning_store: PersistentExperimentLearningStore,
    ) -> None:
        if not isinstance(controller, ExperimentController):
            raise TypeError("controller must be ExperimentController")
        if not isinstance(learning_store, PersistentExperimentLearningStore):
            raise TypeError("learning_store must be PersistentExperimentLearningStore")
        self.controller = controller
        self.learning_store = learning_store
        self.bridge = ExperimentLearningBridge()

    def run(
        self,
        *,
        manifest: ExperimentManifest,
        intervention: LearningIntervention,
        replications: tuple[ExperimentReplication, ...],
        retest_contract: CampaignRetestContract,
        baseline_score: float,
        evidence_refs: Sequence[str],
        domain_lifts: tuple[tuple[str, float], ...] = (),
        episode: EngineeringEpisode | None = None,
    ) -> ExperimentLearningCycleResult:
        if not isinstance(manifest, ExperimentManifest):
            raise TypeError("an ExperimentManifest is required")
        if not isinstance(intervention, LearningIntervention):
            raise TypeError("a LearningIntervention is required")
        if manifest.experiment_id.strip() == "":
            raise ValueError("experiment manifest identity is required")
        if manifest.experiment_id != intervention.intervention_id and not manifest.experiment_id.strip():
            raise LearningStoreConflict("manifest experiment identity mismatch")
        if manifest.intervention_digest != intervention.intervention_digest:
            raise LearningStoreConflict("manifest intervention digest does not match intervention")
        if manifest.capability != intervention.target_capability:
            raise LearningStoreConflict("manifest capability does not match intervention target")
        if retest_contract.intervention_digest != manifest.intervention_digest:
            raise LearningStoreConflict("retest contract does not match the learning manifest")

        result = self.controller.aggregate(
            experiment_id=manifest.experiment_id,
            intervention=intervention,
            replications=replications,
            retest_contract=retest_contract,
            baseline_score=baseline_score,
        )
        signal = self.bridge.derive(
            capability=manifest.capability,
            result=result,
            domain_lifts=domain_lifts,
        )
        # record() is the durable gate. If evidence cannot be verified or storage
        # fails, this cycle raises instead of returning an unpersisted success.
        receipt = self.learning_store.record(
            manifest=manifest,
            result=result,
            signal=signal,
            evidence_refs=evidence_refs,
            episode=episode,
            domain_lifts=domain_lifts,
        )
        return ExperimentLearningCycleResult(result, signal, receipt)


__all__ = ["ExperimentLearningCycle", "ExperimentLearningCycleResult"]
