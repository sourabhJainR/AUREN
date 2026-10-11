"""Runner for externally controlled generalization campaigns.

A non-empty attestation string is only material to verify, not proof of trust.
Before executing any task, the runner requires an injected attestation verifier
that authenticates signer, evaluator, oracle, and exact campaign lineage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .external_environment_protocol import EnvironmentContract, EpisodeTrace, EnvironmentEvaluation, ExternalEnvironmentEvaluator
from .external_evaluation_campaign import CampaignOutcome, ExternalEvaluationCampaign
from .independent_evaluation_attestation import AttestedEvaluation


class ExternalEnvironmentAdapter(Protocol):
    def run(self, case_id: str, contract: EnvironmentContract) -> EpisodeTrace: ...


class IndependentOracle(Protocol):
    def verify(self, trace: EpisodeTrace) -> bool: ...


@dataclass(frozen=True, slots=True)
class GeneralizationCampaignResult:
    campaign: ExternalEvaluationCampaign
    attestation: AttestedEvaluation
    environment: EnvironmentEvaluation
    outcome: CampaignOutcome


class ExternalGeneralizationCampaignRunner:
    """Run an external campaign through explicit, verified trust boundaries."""

    def __init__(
        self,
        environment: ExternalEnvironmentAdapter,
        oracle: IndependentOracle,
        evaluator: ExternalEnvironmentEvaluator | None = None,
        attestation_verifier: Callable[[ExternalEvaluationCampaign], AttestedEvaluation] | None = None,
    ) -> None:
        if not callable(getattr(environment, "run", None)):
            raise TypeError("an external environment adapter is required")
        if not callable(getattr(oracle, "verify", None)):
            raise TypeError("an independent oracle is required")
        if attestation_verifier is not None and not callable(attestation_verifier):
            raise TypeError("attestation_verifier must be callable")
        self.environment = environment
        self.oracle = oracle
        self.evaluator = evaluator or ExternalEnvironmentEvaluator()
        self.attestation_verifier = attestation_verifier

    def _verify_campaign_attestation(self, campaign: ExternalEvaluationCampaign) -> AttestedEvaluation:
        if not campaign.has_attestation_material:
            raise ValueError("external campaign attestation is required")
        if self.attestation_verifier is None:
            raise ValueError("an external campaign attestation verifier is required")
        try:
            verified = self.attestation_verifier(campaign)
        except Exception:
            raise ValueError("external campaign attestation verification failed") from None
        if not isinstance(verified, AttestedEvaluation) or not verified.trustworthy:
            raise ValueError("external campaign attestation is not independently verified")
        attestation = verified.attestation
        if (
            attestation.campaign_digest != campaign.campaign_digest
            or attestation.corpus_digest != campaign.corpus_digest
            or attestation.oracle_digest != campaign.oracle_digest
            or attestation.evaluator_version != campaign.evaluator_version
            or attestation.signature != campaign.external_attestation
        ):
            raise ValueError("verified attestation lineage does not match the external campaign")
        return verified

    def run(
        self,
        campaign: ExternalEvaluationCampaign,
        contract: EnvironmentContract,
        *,
        pass_rule: Callable[[EpisodeTrace], bool] | None = None,
    ) -> GeneralizationCampaignResult:
        if not isinstance(campaign, ExternalEvaluationCampaign):
            raise TypeError("ExternalEvaluationCampaign is required")
        if not isinstance(contract, EnvironmentContract):
            raise TypeError("EnvironmentContract is required")
        if not campaign.structurally_valid:
            raise ValueError("contaminated or structurally invalid campaign cannot run")
        if contract.contract_digest != campaign.corpus_digest:
            raise ValueError("environment contract must match campaign corpus digest")

        # Verify before touching the external environment so an unauthenticated
        # campaign cannot consume resources or cause side effects.
        verified_attestation = self._verify_campaign_attestation(campaign)

        traces = []
        for case_id in campaign.case_ids:
            trace = self.environment.run(case_id, contract)
            if trace.episode_id != case_id:
                raise ValueError("environment returned trace for wrong case")
            verified = self.oracle.verify(trace)
            if type(verified) is not bool or verified != trace.verified:
                raise ValueError("oracle verification disagrees with trace")
            traces.append(trace)

        environment_result = self.evaluator.evaluate(contract, traces)
        rule = pass_rule or (lambda trace: trace.success and trace.verified)
        holdout = set(campaign.holdout_case_ids)
        holdout_traces = [t for t in traces if t.episode_id in holdout]
        if not holdout_traces:
            raise ValueError("campaign has no executed holdout traces")
        holdout_pass = sum(rule(t) is True for t in holdout_traces) / len(holdout_traces)
        verified_rate = sum(t.verified is True for t in holdout_traces) / len(holdout_traces)
        overall_pass = sum(rule(t) is True for t in traces) / len(traces)
        generalization_gap = max(0.0, overall_pass - holdout_pass)
        efficiency = sum(1.0 / max(1, t.steps) for t in holdout_traces) / len(holdout_traces)
        outcome = CampaignOutcome(
            campaign.campaign_digest,
            holdout_pass,
            verified_rate,
            generalization_gap,
            min(1.0, abs(verified_rate - holdout_pass)),
            min(1.0, efficiency),
            tuple(t.episode_id for t in traces if rule(t) is not True),
        )
        return GeneralizationCampaignResult(campaign, verified_attestation, environment_result, outcome)


__all__ = [
    "ExternalEnvironmentAdapter",
    "IndependentOracle",
    "GeneralizationCampaignResult",
    "ExternalGeneralizationCampaignRunner",
]
