"""Freshness and replay protection for external Arena attestations.

This layer wraps an existing attestation rather than changing the wire format.
It binds an issuance timestamp and one-time-use identity to the verified
attestation digest. Replay storage is injected and can be durable/distributed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable

from .attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from .independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation


@dataclass(frozen=True, slots=True)
class FreshEvaluationAttestation:
    attestation: EvaluationAttestation
    issued_at: int

    def __post_init__(self) -> None:
        if self.issued_at < 0:
            raise ValueError("issued_at must be non-negative")

    @property
    def attestation_id(self) -> str:
        payload = {
            "attestation_digest": self.attestation.attestation_digest,
            "issued_at": self.issued_at,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AttestationFreshnessPolicy:
    max_age_seconds: int = 900
    max_future_skew_seconds: int = 30

    def __post_init__(self) -> None:
        if self.max_age_seconds < 0 or self.max_future_skew_seconds < 0:
            raise ValueError("freshness windows must be non-negative")

    def validate(self, attestation: FreshEvaluationAttestation, *, now: int) -> tuple[bool, tuple[str, ...]]:
        if now < 0:
            raise ValueError("now must be non-negative")
        if attestation.issued_at > now + self.max_future_skew_seconds:
            return False, ("attestation issuance time is too far in the future",)
        if now - attestation.issued_at > self.max_age_seconds:
            return False, ("attestation has expired",)
        return True, ()


class AttestationReplayRegistry:
    """One-time-use registry; production deployments can inject durable storage."""

    def __init__(self, seen: set[str] | None = None) -> None:
        self._seen = set(seen or ())

    def claim(self, attestation_id: str) -> bool:
        if not attestation_id.strip():
            raise ValueError("attestation_id is required")
        if attestation_id in self._seen:
            return False
        self._seen.add(attestation_id)
        return True


class FreshnessAwareAttestor:
    """Verify lineage, principal separation, freshness, and replay protection."""

    def __init__(
        self,
        verify_signature: Callable,
        policy: AttestationTrustPolicy,
        freshness_policy: AttestationFreshnessPolicy,
        replay_registry: AttestationReplayRegistry | None = None,
    ) -> None:
        self._attestor = SeparationAwareAttestor(verify_signature, policy)
        self.freshness_policy = freshness_policy
        self.replay_registry = replay_registry or AttestationReplayRegistry()

    def verify(
        self,
        attestation: FreshEvaluationAttestation,
        *,
        evaluator_id: str,
        oracle_id: str,
        expected_campaign_digest: str,
        expected_corpus_digest: str,
        expected_oracle_digest: str,
        now: int,
        contamination_detected: bool = False,
    ) -> AttestedEvaluation:
        base = self._attestor.verify(
            attestation.attestation,
            evaluator_id=evaluator_id,
            oracle_id=oracle_id,
            expected_campaign_digest=expected_campaign_digest,
            expected_corpus_digest=expected_corpus_digest,
            expected_oracle_digest=expected_oracle_digest,
            contamination_detected=contamination_detected,
        )
        fresh, freshness_reasons = self.freshness_policy.validate(attestation, now=now)
        replay_ok = self.replay_registry.claim(attestation.attestation_id) if fresh else False
        reasons = tuple(
            (*base.reasons, *freshness_reasons)
            if replay_ok
            else (*base.reasons, *freshness_reasons, "attestation has already been consumed or is not fresh")
        )
        trustworthy = base.trustworthy and fresh and replay_ok
        return AttestedEvaluation(
            base.attestation,
            trustworthy,
            base.independent,
            base.contamination_free,
            reasons,
            base.evidence_digest,
        )


__all__ = [
    "FreshEvaluationAttestation",
    "AttestationFreshnessPolicy",
    "AttestationReplayRegistry",
    "FreshnessAwareAttestor",
]
