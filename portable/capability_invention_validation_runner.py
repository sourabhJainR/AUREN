"""Execute capability-invention validation plans behind external trust boundaries.

The runner consumes a frozen validation plan, an externally controlled executor,
and an independent oracle. It emits validation evidence only; capability
promotion and lifecycle mutation remain outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from .capability_invention_validation import CapabilityValidationPlan, ValidationProbe
from .external_environment_protocol import EnvironmentContract, EpisodeTrace


class ValidationExecutor(Protocol):
    def run(self, probe: ValidationProbe, contract: EnvironmentContract) -> EpisodeTrace: ...


class ValidationOracle(Protocol):
    independent: bool

    def verify(self, probe: ValidationProbe, trace: EpisodeTrace) -> bool: ...


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    proposal_digest: str
    plan_digest: str
    probe_ids: tuple[str, ...]
    holdout_probe_ids: tuple[str, ...]
    passed_probe_ids: tuple[str, ...]
    verified_probe_ids: tuple[str, ...]
    failed_probe_ids: tuple[str, ...]
    task_families: tuple[str, ...]
    contamination_detected: bool
    independent_oracle: bool
    fresh_holdout: bool
    max_steps: int
    evidence_digest: str

    @property
    def pass_rate(self) -> float:
        return len(self.passed_probe_ids) / len(self.probe_ids) if self.probe_ids else 0.0

    @property
    def holdout_pass_rate(self) -> float:
        return len(self.passed_probe_ids) / len(self.holdout_probe_ids) if self.holdout_probe_ids else 0.0

    @property
    def trustworthy(self) -> bool:
        return not self.contamination_detected and self.independent_oracle and self.fresh_holdout and bool(self.evidence_digest)


@dataclass(frozen=True, slots=True)
class CapabilityValidationResult:
    evidence: ValidationEvidence
    traces: tuple[EpisodeTrace, ...]


class CapabilityInventionValidationRunner:
    """Run a frozen invention plan without acquiring promotion authority."""

    def __init__(self, executor: ValidationExecutor, oracle: ValidationOracle, *, max_probes: int = 100) -> None:
        if max_probes < 1:
            raise ValueError("max_probes must be positive")
        self.executor = executor
        self.oracle = oracle
        self.max_probes = max_probes

    @staticmethod
    def _digest(evidence: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def run(
        self,
        plan: CapabilityValidationPlan,
        contract: EnvironmentContract,
        *,
        expected_plan_digest: str | None = None,
        max_steps: int | None = None,
        contaminated: bool = False,
    ) -> CapabilityValidationResult:
        actual_plan_digest = plan.plan_digest
        if expected_plan_digest is not None and expected_plan_digest != actual_plan_digest:
            raise ValueError("validation plan digest mismatch")
        if not plan.independent or not plan.fresh_holdout_required:
            raise ValueError("validation plan must require an independent fresh holdout")
        if not getattr(self.oracle, "independent", False):
            raise ValueError("validation oracle must be independent")
        if contaminated:
            raise ValueError("contaminated validation cannot run")
        if len(plan.probes) > self.max_probes:
            raise ValueError("validation plan exceeds probe budget")
        if not plan.probes or any(not p.holdout or not p.oracle_required for p in plan.probes):
            raise ValueError("every invention validation probe must be an oracle-backed holdout")
        probe_ids = tuple(p.probe_id for p in plan.probes)
        if len(set(probe_ids)) != len(probe_ids):
            raise ValueError("validation plan contains duplicate probe ids")
        holdout_ids = tuple(p.probe_id for p in plan.probes if p.holdout)
        if len(holdout_ids) != len(probe_ids):
            raise ValueError("all invention validation probes must remain holdouts")
        families = tuple(dict.fromkeys(p.task_family for p in plan.probes))
        if len(families) < 2:
            raise ValueError("validation requires at least two task families")
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be positive")
        execution_contract = contract
        if max_steps is not None and contract.max_steps > max_steps:
            execution_contract = EnvironmentContract(
                contract.environment_id, contract.version,
                contract.observation_modalities, contract.action_types,
                max_steps, contract.reset_between_cases, contract.hidden_state,
                contract.external_tools, contract.unfamiliar_tools,
            )
        traces = []
        passed = []
        verified = []
        failed = []
        for probe in plan.probes:
            trace = self.executor.run(probe, execution_contract)
            if trace.episode_id != probe.probe_id:
                raise ValueError("executor returned trace for wrong probe")
            if trace.environment_digest != execution_contract.contract_digest:
                raise ValueError("trace does not match validation environment")
            if trace.steps > execution_contract.max_steps:
                raise ValueError("validation probe exceeded step budget")
            oracle_result = self.oracle.verify(probe, trace)
            if oracle_result != trace.verified:
                raise ValueError("independent oracle disagrees with trace")
            traces.append(trace)
            if trace.verified:
                verified.append(probe.probe_id)
            if trace.success and trace.verified:
                passed.append(probe.probe_id)
            else:
                failed.append(probe.probe_id)
        payload = {
            "proposal_digest": plan.proposal_digest,
            "plan_digest": actual_plan_digest,
            "probe_ids": probe_ids,
            "holdout_probe_ids": holdout_ids,
            "passed_probe_ids": tuple(passed),
            "verified_probe_ids": tuple(verified),
            "failed_probe_ids": tuple(failed),
            "task_families": families,
            "contamination_detected": False,
            "independent_oracle": True,
            "fresh_holdout": True,
            "max_steps": execution_contract.max_steps,
        }
        evidence = ValidationEvidence(**payload, evidence_digest=self._digest(payload))
        return CapabilityValidationResult(evidence, tuple(traces))


__all__ = [
    "ValidationExecutor",
    "ValidationOracle",
    "ValidationEvidence",
    "CapabilityValidationResult",
    "CapabilityInventionValidationRunner",
]
