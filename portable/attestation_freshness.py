"""Freshness and replay protection for external Arena attestations.

This layer wraps an existing attestation rather than changing the wire format.
It binds issuance time, freshness policy, and one-time-use outcome to the
verified evidence digest. Principal authentication is injected and explicit.
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
        if not isinstance(self.attestation, EvaluationAttestation):
            raise TypeError("EvaluationAttestation is required")
        if type(self.issued_at) is not int or self.issued_at < 0:
            raise ValueError("issued_at must be a non-negative integer")

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
        if type(self.max_age_seconds) is not int or type(self.max_future_skew_seconds) is not int:
            raise ValueError("freshness windows must be integers")
        if self.max_age_seconds < 0 or self.max_future_skew_seconds < 0:
            raise ValueError("freshness windows must be non-negative")

    def validate(self, attestation: FreshEvaluationAttestation, *, now: int) -> tuple[bool, tuple[str, ...]]:
        if type(now) is not int or now < 0:
            raise ValueError("now must be a non-negative integer")
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
        if not isinstance(attestation_id, str) or not attestation_id.strip():
            raise ValueError("attestation_id is required")
        if attestation_id in self._seen:
            return False
        self._seen.add(attestation_id)
        return True


class FreshnessAwareAttestor:
    """Verify lineage, authenticated principal separation, freshness, and replay protection."""

    def __init__(
        self,
        verify_signature: Callable,
        policy: AttestationTrustPolicy,
        freshness_policy: AttestationFreshnessPolicy,
        replay_registry: AttestationReplayRegistry | None = None,
        verify_principal: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not isinstance(freshness_policy, AttestationFreshnessPolicy):
            raise TypeError("AttestationFreshnessPolicy is required")
        self._attestor = SeparationAwareAttestor(verify_signature, policy, verify_principal)
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
        if not isinstance(attestation, FreshEvaluationAttestation):
            raise TypeError("FreshEvaluationAttestation is required")
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
        # Invalid signatures/lineage must not consume the one-time replay slot:
        # otherwise an unauthenticated request could preempt a valid attestation.
        replay_ok = self.replay_registry.claim(attestation.attestation_id) if fresh and base.trustworthy else False
        reasons = list((*base.reasons, *freshness_reasons))
        if fresh and base.trustworthy and not replay_ok:
            reasons.append("attestation has already been consumed")
        trustworthy = base.trustworthy and fresh and replay_ok
        evidence_payload = {
            "base_evidence_digest": base.evidence_digest,
            "attestation_id": attestation.attestation_id,
            "issued_at": attestation.issued_at,
            "verified_at": now,
            "max_age_seconds": self.freshness_policy.max_age_seconds,
            "max_future_skew_seconds": self.freshness_policy.max_future_skew_seconds,
            "fresh": fresh,
            "replay_accepted": replay_ok,
            "trustworthy": trustworthy,
        }
        evidence_digest = hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return AttestedEvaluation(
            base.attestation,
            trustworthy,
            base.independent,
            base.contamination_free,
            tuple(reasons),
            evidence_digest,
        )


__all__ = [
    "FreshEvaluationAttestation",
    "AttestationFreshnessPolicy",
    "AttestationReplayRegistry",
    "FreshnessAwareAttestor",
]
