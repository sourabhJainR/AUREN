"""Evidence-driven bridge from experiment outcomes to future curriculum choices.

The bridge records a bounded learning signal for curriculum selection. It does
not execute tasks, alter capabilities, or promote interventions.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json

from .adaptive_experiment_controller import AdaptiveExperimentResult


@dataclass(frozen=True, slots=True)
class CurriculumLearningSignal:
    experiment_id: str
    capability: str
    action: str
    failed_domains: tuple[str, ...]
    next_conditions: tuple[str, ...]
    evidence_digest: str

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.capability.strip():
            raise ValueError("experiment_id and capability are required")
        if self.action not in {"continue", "rollback", "causal-review", "insufficient-evidence"}:
            raise ValueError("unsupported learning action")

    @property
    def signal_digest(self) -> str:
        payload={k:getattr(self,k) for k in ("experiment_id","capability","action","failed_domains","next_conditions","evidence_digest")}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


class ExperimentLearningBridge:
    """Translate experiment evidence into bounded curriculum signals."""

    def derive(
        self,
        *,
        capability: str,
        result: AdaptiveExperimentResult,
        domain_lifts: tuple[tuple[str, float], ...] = (),
    ) -> CurriculumLearningSignal:
        if not capability.strip():
            raise ValueError("capability is required")
        failed=tuple(sorted(domain for domain,lift in domain_lifts if lift < 0.02))
        if result.rollback_recommended:
            action="rollback"
            conditions=("regression-recovery", "adversarial-recheck")
        elif result.evidence_sufficient:
            action="causal-review"
            conditions=("cross-domain-transfer", "long-horizon-recheck")
        elif result.replication_count == 0 or result.replication_count < 3:
            action="insufficient-evidence"
            conditions=("replicate", "new-holdout")
        else:
            action="continue"
            conditions=("novel-input", "constraint-shift")
        return CurriculumLearningSignal(
            result.experiment_id, capability, action, failed, conditions, result.result_digest
        )


__all__=["CurriculumLearningSignal","ExperimentLearningBridge"]
