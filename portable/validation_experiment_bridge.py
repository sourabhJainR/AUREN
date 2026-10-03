"""Bridge invention validation evidence into a bounded experiment proposal.

Validation proves that an invention candidate can be exercised and independently
checked. It is not causal evidence. This module converts trustworthy validation
evidence into a proposal for a fresh control/treatment experiment while keeping
execution and promotion authority outside the bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .capability_invention_validation_runner import ValidationEvidence
from .external_evaluation_campaign import CampaignRetestContract, LearningIntervention


@dataclass(frozen=True, slots=True)
class ValidationExperimentProposal:
    experiment_id: str
    capability: str
    intervention: LearningIntervention
    prior_validation_digest: str
    retest_contract: CampaignRetestContract
    required_domains: tuple[str, ...]
    required_holdout: bool
    required_independent_oracle: bool
    minimum_replications: int
    proposal_digest: str

    @property
    def ready_for_execution(self) -> bool:
        return (
            bool(self.required_domains)
            and len(self.required_domains) >= 2
            and self.required_holdout
            and self.required_independent_oracle
            and self.minimum_replications >= 3
        )


class ValidationExperimentBridge:
    """Create experiment specifications from trustworthy validation evidence."""

    def propose(
        self,
        *,
        capability: str,
        evidence: ValidationEvidence,
        intervention: LearningIntervention,
        prior_campaign_digest: str,
        experiment_id: str | None = None,
        minimum_replications: int = 3,
    ) -> ValidationExperimentProposal:
        if not capability.strip():
            raise ValueError("capability is required")
        if not evidence.trustworthy:
            raise ValueError("only trustworthy validation evidence can enter experiments")
        if evidence.holdout_pass_rate < 0.75:
            raise ValueError("validation pass rate is below invention acceptance threshold")
        if evidence.proposal_digest == "":
            raise ValueError("validation evidence must identify the invention")
        if not prior_campaign_digest.strip():
            raise ValueError("prior campaign digest is required")
        if minimum_replications < 3:
            raise ValueError("at least three independent replications are required")
        domains = evidence.task_families
        if len(domains) < 2:
            raise ValueError("cross-domain experiment requires at least two task families")
        experiment_id = experiment_id or self._experiment_id(evidence, intervention)
        retest = CampaignRetestContract(
            prior_campaign_digest=prior_campaign_digest,
            intervention_digest=intervention.intervention_digest,
            new_holdout_required=True,
            independence_required=True,
        )
        payload = {
            "experiment_id": experiment_id,
            "capability": capability,
            "intervention_digest": intervention.intervention_digest,
            "prior_validation_digest": evidence.evidence_digest,
            "retest_contract": {
                "prior_campaign_digest": retest.prior_campaign_digest,
                "intervention_digest": retest.intervention_digest,
            },
            "required_domains": domains,
            "required_holdout": True,
            "required_independent_oracle": True,
            "minimum_replications": minimum_replications,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ValidationExperimentProposal(
            experiment_id, capability, intervention, evidence.evidence_digest,
            retest, domains, True, True, minimum_replications, digest,
        )

    @staticmethod
    def _experiment_id(
        evidence: ValidationEvidence,
        intervention: LearningIntervention,
    ) -> str:
        seed = evidence.evidence_digest + "|" + intervention.intervention_digest
        return "experiment-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


__all__ = ["ValidationExperimentProposal", "ValidationExperimentBridge"]
