"""Runner for externally controlled generalization campaigns.

The runner orchestrates an externally supplied environment adapter and oracle
without owning either one. It emits traces and a campaign outcome; it never
promotes capabilities or persists benchmark answers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .external_environment_protocol import EnvironmentContract, EpisodeTrace, EnvironmentEvaluation, ExternalEnvironmentEvaluator
from .external_evaluation_campaign import CampaignOutcome, ExternalEvaluationCampaign


class ExternalEnvironmentAdapter(Protocol):
    def run(self, case_id: str, contract: EnvironmentContract) -> EpisodeTrace: ...


class IndependentOracle(Protocol):
    def verify(self, trace: EpisodeTrace) -> bool: ...


@dataclass(frozen=True, slots=True)
class GeneralizationCampaignResult:
    campaign: ExternalEvaluationCampaign
    environment: EnvironmentEvaluation
    outcome: CampaignOutcome


class ExternalGeneralizationCampaignRunner:
    """Run an external campaign through explicit trust boundaries."""

    def __init__(
        self,
        environment: ExternalEnvironmentAdapter,
        oracle: IndependentOracle,
        evaluator: ExternalEnvironmentEvaluator | None = None,
    ) -> None:
        self.environment = environment
        self.oracle = oracle
        self.evaluator = evaluator or ExternalEnvironmentEvaluator()

    def run(
        self,
        campaign: ExternalEvaluationCampaign,
        contract: EnvironmentContract,
        *,
        pass_rule: Callable[[EpisodeTrace], bool] | None = None,
    ) -> GeneralizationCampaignResult:
        if campaign.contamination_detected:
            raise ValueError("contaminated campaign cannot run")
        if contract.contract_digest != campaign.corpus_digest:
            raise ValueError("environment contract must match campaign corpus digest")

        traces = []
        for case_id in campaign.case_ids:
            trace = self.environment.run(case_id, contract)
            if trace.episode_id != case_id:
                raise ValueError("environment returned trace for wrong case")
            verified = self.oracle.verify(trace)
            if verified != trace.verified:
                raise ValueError("oracle verification disagrees with trace")
            traces.append(trace)

        environment_result = self.evaluator.evaluate(contract, traces)
        rule = pass_rule or (lambda trace: trace.success and trace.verified)
        holdout = set(campaign.holdout_case_ids)
        holdout_traces = [t for t in traces if t.episode_id in holdout]
        if not holdout_traces:
            raise ValueError("campaign has no executed holdout traces")
        holdout_pass = sum(rule(t) for t in holdout_traces) / len(holdout_traces)
        verified_rate = sum(t.verified for t in holdout_traces) / len(holdout_traces)
        overall_pass = sum(rule(t) for t in traces) / len(traces)
        generalization_gap = max(0.0, overall_pass - holdout_pass)
        efficiency = sum(
            1.0 / max(1, t.steps) for t in holdout_traces
        ) / len(holdout_traces)
        outcome = CampaignOutcome(
            campaign.campaign_digest,
            holdout_pass,
            verified_rate,
            generalization_gap,
            min(1.0, abs(verified_rate - holdout_pass)),
            min(1.0, efficiency),
            tuple(t.episode_id for t in traces if not rule(t)),
        )
        return GeneralizationCampaignResult(campaign, environment_result, outcome)


__all__ = [
    "ExternalEnvironmentAdapter",
    "IndependentOracle",
    "GeneralizationCampaignResult",
    "ExternalGeneralizationCampaignRunner",
]
