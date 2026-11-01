"""Connect immutable experiment manifests/outcomes to AUREN's canonical learning graph.

The reproducible ledger is an execution record; PersistentExperimentLearningStore
remains the authority for evidence verification, candidate disposition, promotion,
retraction and future memory retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .adaptive_experiment_controller import AdaptiveExperimentResult
from .engineering_episode import EngineeringEpisode
from .experiment_learning_bridge import CurriculumLearningSignal, ExperimentLearningBridge
from .persistent_experiment_learning import (
    ExperimentManifest as CanonicalExperimentManifest,
    PersistentExperimentLearningStore,
    PersistentLearningReceipt,
)
from .reproducible_experiment import (
    ExperimentManifest as ReproducibleManifest,
    ReproducibleExperimentStore,
)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pin_digest(value: str) -> str:
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return _sha256({"source_value": value})


@dataclass(frozen=True, slots=True)
class CanonicalExperimentLink:
    reproducible_manifest_digest: str
    reproducible_outcome_digest: str
    learning_receipt: PersistentLearningReceipt
    signal: CurriculumLearningSignal


class ReproducibleLearningGraphBridge:
    """Persist the same experiment in the immutable ledger and canonical memory graph.

    Writes are idempotent across process interruption. If the process stops between
    stores, call record() again with the same pinned inputs; changed inputs conflict.
    The canonical learning store alone decides whether evidence is verified and
    whether a candidate may later be promoted into reusable memory.
    """

    def __init__(
        self,
        *,
        experiment_store: ReproducibleExperimentStore,
        learning_store: PersistentExperimentLearningStore,
    ) -> None:
        if not isinstance(experiment_store, ReproducibleExperimentStore):
            raise TypeError("experiment_store must be ReproducibleExperimentStore")
        if not isinstance(learning_store, PersistentExperimentLearningStore):
            raise TypeError("learning_store must be PersistentExperimentLearningStore")
        self.experiment_store = experiment_store
        self.learning_store = learning_store

    def record(
        self,
        *,
        manifest: CanonicalExperimentManifest,
        result: AdaptiveExperimentResult,
        baseline_digest: str,
        seed: int,
        outcome_status: str,
        metrics: Mapping[str, Any],
        evidence_refs: Sequence[str],
        episode: EngineeringEpisode | None = None,
        domain_lifts: tuple[tuple[str, float], ...] = (),
        parent_digest: str = "",
    ) -> CanonicalExperimentLink:
        if not isinstance(manifest, CanonicalExperimentManifest):
            raise TypeError("canonical ExperimentManifest is required")
        if not isinstance(result, AdaptiveExperimentResult):
            raise TypeError("AdaptiveExperimentResult is required")
        if result.experiment_id != manifest.experiment_id:
            raise ValueError("experiment identity differs between canonical manifest and result")
        if result.intervention_digest != manifest.intervention_digest:
            raise ValueError("candidate/intervention digest differs from the canonical manifest")
        if outcome_status not in {"passed", "failed", "cancelled", "timed_out", "unknown", "unscored"}:
            raise ValueError("unsupported experiment outcome status")
        if outcome_status == "passed" and (
            not result.evidence_sufficient or result.regression_detected or result.rollback_recommended
        ):
            raise ValueError("passed outcome conflicts with the pinned experiment result")
        if not isinstance(baseline_digest, str) or len(baseline_digest) != 64 or any(
            c not in "0123456789abcdef" for c in baseline_digest
        ):
            raise ValueError("baseline_digest must be a lowercase SHA-256 digest")
        if not isinstance(metrics, Mapping):
            raise ValueError("metrics must be a mapping")
        if isinstance(evidence_refs, (str, bytes)) or not isinstance(evidence_refs, Sequence):
            raise ValueError("evidence_refs must be a sequence of external evidence references")
        refs = tuple(dict.fromkeys(evidence_refs))
        if not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError("at least one non-empty evidence reference is required")
        if episode is not None and (
            not isinstance(episode, EngineeringEpisode) or episode.project != self.learning_store.project
        ):
            raise ValueError("episode must belong to the canonical learning project")

        pinned_environment = _sha256({
            "environment_digest": manifest.environment_digest,
            "policy_digest": manifest.policy_digest,
            "budget_digest": manifest.budget_digest,
            "executor_id": manifest.executor_id,
            "evaluator_id": manifest.evaluator_id,
        })
        reproducible = ReproducibleManifest(
            experiment_id=manifest.experiment_id,
            task_manifest_digest=_pin_digest(manifest.task_digest),
            seed=seed,
            split_digest=_pin_digest(manifest.task_split_digest),
            candidate_digest=_pin_digest(manifest.intervention_digest),
            baseline_digest=baseline_digest,
            harness_digest=_pin_digest(manifest.harness_digest),
            environment_digest=pinned_environment,
            model_provider=manifest.provider_id,
            model_version=manifest.model_id,
            canonical_manifest_digest=manifest.digest,
            policy_digest=manifest.policy_digest,
            budget_digest=manifest.budget_digest,
            executor_id=manifest.executor_id,
            evaluator_id=manifest.evaluator_id,
        )
        reproducible_digest = self.experiment_store.record_manifest(
            reproducible, parent_digest=parent_digest,
        )
        outcome_digest = self.experiment_store.record_outcome(
            outcome_id="outcome:" + result.result_digest,
            manifest=reproducible,
            status=outcome_status,
            evidence_refs=refs,
            metrics=dict(metrics),
        )
        signal = ExperimentLearningBridge().derive(
            capability=manifest.capability, result=result, domain_lifts=domain_lifts,
        )
        receipt = self.learning_store.record(
            manifest=manifest,
            result=result,
            signal=signal,
            evidence_refs=refs,
            episode=episode,
            domain_lifts=domain_lifts,
        )

        graph = self.learning_store.graph
        canonical_manifest_node = graph.add_node("experiment_manifest", manifest.digest)
        reproducible_manifest_node = graph.add_node(
            "reproducible_experiment_manifest", reproducible_digest,
            {
                "experiment_id": manifest.experiment_id,
                "canonical_manifest_digest": manifest.digest,
                "policy_digest": manifest.policy_digest,
                "budget_digest": manifest.budget_digest,
                "executor_id": manifest.executor_id,
                "evaluator_id": manifest.evaluator_id,
            },
        )
        outcome_node = graph.add_node(
            "reproducible_experiment_outcome", outcome_digest,
            {"experiment_id": manifest.experiment_id, "status": outcome_status},
        )
        receipt_node = graph.add_node(
            "learning_receipt", receipt.receipt_id,
            {"disposition": receipt.disposition, "capability": receipt.capability},
        )
        graph.add_edge(canonical_manifest_node, "pinned_by", reproducible_manifest_node)
        graph.add_edge(reproducible_manifest_node, "produced_outcome", outcome_node)
        graph.add_edge(outcome_node, "recorded_as_learning", receipt_node)
        return CanonicalExperimentLink(
            reproducible_manifest_digest=reproducible_digest,
            reproducible_outcome_digest=outcome_digest,
            learning_receipt=receipt,
            signal=signal,
        )


__all__ = ["CanonicalExperimentLink", "ReproducibleLearningGraphBridge"]
