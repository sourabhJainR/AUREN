"""Longitudinal transfer evidence for independent capability evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class TransferObservation:
    campaign_digest: str
    task_family: str
    domain: str
    split: str
    pass_rate: float
    verification_rate: float
    oracle_independent: bool = True
    contaminated: bool = False

    def __post_init__(self) -> None:
        if not self.campaign_digest.strip() or not self.task_family.strip() or not self.domain.strip():
            raise ValueError("campaign_digest, task_family, and domain are required")
        if self.split not in {"train", "holdout", "novel"}:
            raise ValueError("split must be train, holdout, or novel")
        for name, value in (("pass_rate", self.pass_rate), ("verification_rate", self.verification_rate)):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class LongitudinalTransferProfile:
    capability: str
    observations: tuple[TransferObservation, ...]
    independent_domains: int
    novel_domain_pass_rate: float
    holdout_pass_rate: float
    transfer_gap: float
    trustworthy: bool
    profile_digest: str


class LongitudinalTransferEvaluator:
    """Summarize independent, non-contaminated transfer without claiming AGI."""

    def evaluate(self, capability: str, observations: Sequence[TransferObservation]) -> LongitudinalTransferProfile:
        if not capability.strip() or not observations:
            raise ValueError("capability and observations are required")
        trusted = tuple(
            o for o in observations
            if o.oracle_independent and not o.contaminated
        )
        if not trusted:
            raise ValueError("no trustworthy independent observations")
        novel = tuple(o for o in trusted if o.split == "novel")
        holdout = tuple(o for o in trusted if o.split == "holdout")
        domains = {o.domain for o in novel}
        novel_rate = sum(o.pass_rate for o in novel) / len(novel) if novel else 0.0
        holdout_rate = sum(o.pass_rate for o in holdout) / len(holdout) if holdout else 0.0
        transfer_gap = max(0.0, holdout_rate - novel_rate) if novel else 1.0
        payload = {
            "capability": capability,
            "observations": [o.__dict__ if hasattr(o, "__dict__") else {
                "campaign_digest": o.campaign_digest, "task_family": o.task_family,
                "domain": o.domain, "split": o.split, "pass_rate": o.pass_rate,
                "verification_rate": o.verification_rate,
                "oracle_independent": o.oracle_independent, "contaminated": o.contaminated
            } for o in trusted],
            "independent_domains": len(domains),
            "novel_domain_pass_rate": round(novel_rate, 4),
            "holdout_pass_rate": round(holdout_rate, 4),
            "transfer_gap": round(transfer_gap, 4),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return LongitudinalTransferProfile(
            capability, trusted, len(domains), round(novel_rate, 4),
            round(holdout_rate, 4), round(transfer_gap, 4),
            len(domains) >= 2 and bool(novel) and bool(holdout),
            digest,
        )
