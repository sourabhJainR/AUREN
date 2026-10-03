"""Execute an evidence-bound autonomous curriculum campaign.

The orchestrator consumes a frozen curriculum plan and externally supplied
builders/executors/oracles. It coordinates evaluation only: it does not own
benchmark answers, create oracles, mutate capabilities, or promote lifecycle
state.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Protocol

from .autonomous_curriculum_evolution import CurriculumPlan
from .capability_invention_validation import CapabilityValidationPlan
from .capability_invention_validation_runner import CapabilityInventionValidationRunner, CapabilityValidationResult
from .external_environment_protocol import EnvironmentContract
from .external_evaluation_campaign import CampaignOutcome


class CurriculumTargetGenerator(Protocol):
    def build(self, target_id: str, *, plan_digest: str) -> tuple[CapabilityValidationPlan, EnvironmentContract, str, str]: ...


class CurriculumCampaignRecovery(Protocol):
    def recover(self, target_id: str, error: Exception) -> bool: ...


@dataclass(frozen=True, slots=True)
class CurriculumTargetResult:
    target_id: str
    plan_digest: str
    validation: CapabilityValidationResult | None
    outcome: CampaignOutcome | None
    recovered: bool
    error: str = ""

    @property
    def successful(self) -> bool:
        return self.outcome is not None and self.outcome.holdout_verification_rate > 0

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return self.validation.evidence.probe_ids if self.validation else ()


@dataclass(frozen=True, slots=True)
class ExternalCurriculumCampaignResult:
    campaign_id: str
    curriculum_plan_digest: str
    target_results: tuple[CurriculumTargetResult, ...]
    generator_digest: str
    oracle_digests: tuple[str, ...]
    campaign_digest: str

    @property
    def trustworthy(self) -> bool:
        return (
            bool(self.target_results)
            and all(r.outcome is not None and not r.error for r in self.target_results)
            and bool(self.generator_digest)
            and bool(self.oracle_digests)
            and len(self.oracle_digests) == len(self.target_results)
        )

    @property
    def holdout_pass_rate(self) -> float:
        outcomes = [r.outcome for r in self.target_results if r.outcome is not None]
        return sum(x.holdout_pass_rate for x in outcomes) / len(outcomes) if outcomes else 0.0

    @property
    def failed_target_ids(self) -> tuple[str, ...]:
        return tuple(r.target_id for r in self.target_results if r.outcome is None or r.outcome.holdout_pass_rate < 1.0)


class ExternalCurriculumCampaignOrchestrator:
    """Run each curriculum target through an externally controlled validator."""

    def __init__(self, generator: CurriculumTargetGenerator, runner: CapabilityInventionValidationRunner, *, max_targets: int = 8, recovery: CurriculumCampaignRecovery | None = None) -> None:
        if max_targets < 1:
            raise ValueError("max_targets must be positive")
        self.generator, self.runner, self.max_targets, self.recovery = generator, runner, max_targets, recovery

    @staticmethod
    def _digest(payload: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def run(self, curriculum: CurriculumPlan, *, expected_plan_digest: str | None = None, max_steps: int | None = None, campaign_id: str | None = None) -> ExternalCurriculumCampaignResult:
        if expected_plan_digest is not None and expected_plan_digest != curriculum.plan_digest:
            raise ValueError("curriculum plan digest mismatch")
        if not curriculum.trustworthy:
            raise ValueError("curriculum plan is not trustworthy")
        if len(curriculum.entries) > self.max_targets:
            raise ValueError("curriculum exceeds target budget")
        if len({e.target_id for e in curriculum.entries}) != len(curriculum.entries):
            raise ValueError("curriculum contains duplicate targets")
        results, generator_digests, oracle_digests = [], [], []
        for entry in curriculum.entries:
            recovered = False
            try:
                plan, contract, generator_digest, oracle_digest = self.generator.build(entry.target_id, plan_digest=curriculum.plan_digest)
                if not generator_digest.strip() or not oracle_digest.strip():
                    raise ValueError("external generator and oracle digests are required")
                generator_digests.append(generator_digest)
                oracle_digests.append(oracle_digest)
                validation = self.runner.run(plan, contract, expected_plan_digest=plan.plan_digest, max_steps=max_steps)
                evidence = validation.evidence
                outcome = CampaignOutcome(
                    campaign_digest=evidence.evidence_digest,
                    holdout_pass_rate=evidence.holdout_pass_rate,
                    holdout_verification_rate=len(evidence.verified_probe_ids) / len(evidence.holdout_probe_ids),
                    generalization_gap=max(0.0, evidence.pass_rate - evidence.holdout_pass_rate),
                    calibration_error=1.0 - len(evidence.verified_probe_ids) / len(evidence.holdout_probe_ids),
                    efficiency_score=1.0,
                )
                results.append(CurriculumTargetResult(entry.target_id, curriculum.plan_digest, validation, outcome, recovered))
            except Exception as error:
                if self.recovery is not None:
                    recovered = bool(self.recovery.recover(entry.target_id, error))
                results.append(CurriculumTargetResult(entry.target_id, curriculum.plan_digest, None, None, recovered, str(error)))
        if not results:
            raise ValueError("curriculum must contain at least one target")
        generator_digest = hashlib.sha256("|".join(sorted(generator_digests)).encode()).hexdigest() if len(generator_digests) == len(results) else ""
        payload = {
            "campaign_id": campaign_id or "curriculum-" + curriculum.plan_digest[:16],
            "curriculum_plan_digest": curriculum.plan_digest,
            "target_ids": tuple(r.target_id for r in results),
            "generator_digest": generator_digest,
            "oracle_digests": tuple(oracle_digests),
            "target_evidence": tuple(r.outcome.campaign_digest if r.outcome else "" for r in results),
        }
        return ExternalCurriculumCampaignResult(payload["campaign_id"], curriculum.plan_digest, tuple(results), generator_digest, tuple(oracle_digests), self._digest(payload))


__all__ = ["CurriculumTargetGenerator", "CurriculumCampaignRecovery", "CurriculumTargetResult", "ExternalCurriculumCampaignResult", "ExternalCurriculumCampaignOrchestrator"]
