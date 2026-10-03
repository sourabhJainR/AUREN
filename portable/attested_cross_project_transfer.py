"""Attested cross-project capability transfer evidence.

General intelligence should transfer beyond the repository that produced the
capability. This protocol requires distinct source/target projects, external
attestation on the evaluation evidence, independent holdouts, and a bounded
regression budget before declaring transfer evidence trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib, json
from typing import Sequence

from .independent_evaluation_attestation import AttestedEvaluation


@dataclass(frozen=True, slots=True)
class ProjectTransferObservation:
    source_project: str
    target_project: str
    capability: str
    success: bool
    regression: bool
    holdout: bool
    independent_oracle: bool
    attestation_digest: str

    def __post_init__(self) -> None:
        if not self.source_project.strip() or not self.target_project.strip():
            raise ValueError("source and target projects are required")
        if self.source_project == self.target_project:
            raise ValueError("source and target projects must differ")
        if not self.capability.strip() or not self.attestation_digest.strip():
            raise ValueError("capability and attestation digest are required")


@dataclass(frozen=True, slots=True)
class CrossProjectTransferAssessment:
    source_project: str
    target_project: str
    capability: str
    samples: int
    transfer_rate: float
    regressions: int
    independent_holdouts: int
    trustworthy: bool
    assessment_digest: str


class AttestedCrossProjectTransferEvaluator:
    """Evaluate capability transfer using only attested independent holdouts."""

    def evaluate(
        self,
        observations: Sequence[ProjectTransferObservation],
        attestations: Sequence[AttestedEvaluation],
        *,
        min_samples: int = 5,
        min_transfer_rate: float = 0.8,
        max_regressions: int = 0,
    ) -> CrossProjectTransferAssessment:
        if min_samples < 1 or not 0 <= min_transfer_rate <= 1 or max_regressions < 0:
            raise ValueError("invalid transfer thresholds")
        if not observations:
            raise ValueError("observations are required")
        attest_by_digest={a.evidence_digest:a for a in attestations if a.trustworthy}
        trusted=[
            o for o in observations
            if o.holdout and o.independent_oracle
            and o.attestation_digest in attest_by_digest
        ]
        projects={(o.source_project,o.target_project) for o in trusted}
        if not projects:
            raise ValueError("no attested independent transfer observations")
        source,target=sorted(projects)[0]
        if any((o.source_project,o.target_project)!=(source,target) for o in trusted):
            raise ValueError("mixed project pairs require separate evaluations")
        capabilities={o.capability for o in trusted}
        if len(capabilities)!=1:
            raise ValueError("mixed capabilities require separate evaluations")
        capability=next(iter(capabilities))
        samples=len(trusted)
        rate=sum(o.success for o in trusted)/samples
        regressions=sum(o.regression for o in trusted)
        holdouts=sum(o.holdout and o.independent_oracle for o in trusted)
        trustworthy=samples>=min_samples and rate>=min_transfer_rate and regressions<=max_regressions
        payload={
            "source_project":source,"target_project":target,"capability":capability,
            "samples":samples,"transfer_rate":round(rate,4),"regressions":regressions,
            "independent_holdouts":holdouts,
            "attestations":sorted(o.attestation_digest for o in trusted),
        }
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return CrossProjectTransferAssessment(source,target,capability,samples,round(rate,4),regressions,holdouts,trustworthy,digest)


__all__=["ProjectTransferObservation","CrossProjectTransferAssessment","AttestedCrossProjectTransferEvaluator"]
