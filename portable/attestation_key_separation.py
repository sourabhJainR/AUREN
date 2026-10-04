"""Explicit principal/key-separation policy for external Arena attestations.

Cryptographic verification alone is insufficient if the same principal can act
as evaluator, oracle, and attestation signer. This module makes those trust
relationships explicit while keeping cryptography deployment-injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from .independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation, IndependentEvaluationAttestor


@dataclass(frozen=True, slots=True)
class AttestationTrustPolicy:
    trusted_signers: FrozenSet[str]
    require_distinct_signer: bool = True
    require_distinct_evaluator_oracle: bool = True

    def __post_init__(self) -> None:
        if not self.trusted_signers:
            raise ValueError("at least one trusted attestation signer is required")
        if any(not signer.strip() for signer in self.trusted_signers):
            raise ValueError("trusted signer ids must be non-empty")

    def validate(self, attestation: EvaluationAttestation, *, evaluator_id: str, oracle_id: str) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if not evaluator_id.strip() or not oracle_id.strip():
            raise ValueError("evaluator_id and oracle_id are required")
        if self.require_distinct_evaluator_oracle and evaluator_id == oracle_id:
            reasons.append("evaluator and oracle principals must be distinct")
        if attestation.signer_id not in self.trusted_signers:
            reasons.append("attestation signer is not trusted")
        if self.require_distinct_signer and attestation.signer_id in {evaluator_id, oracle_id}:
            reasons.append("attestation signer must be distinct from evaluator and oracle")
        return not reasons, tuple(reasons)


class SeparationAwareAttestor:
    """Verify an attestation plus explicit evaluator/oracle/signer separation."""

    def __init__(self, verify_signature, policy: AttestationTrustPolicy) -> None:
        self._attestor = IndependentEvaluationAttestor(verify_signature)
        self.policy = policy

    def verify(
        self,
        attestation: EvaluationAttestation,
        *,
        evaluator_id: str,
        oracle_id: str,
        expected_campaign_digest: str,
        expected_corpus_digest: str,
        expected_oracle_digest: str,
        contamination_detected: bool = False,
    ) -> AttestedEvaluation:
        base = self._attestor.verify(
            attestation,
            expected_campaign_digest=expected_campaign_digest,
            expected_corpus_digest=expected_corpus_digest,
            expected_oracle_digest=expected_oracle_digest,
            contamination_detected=contamination_detected,
        )
        separated, separation_reasons = self.policy.validate(attestation, evaluator_id=evaluator_id, oracle_id=oracle_id)
        if separated:
            return base
        reasons = tuple((*base.reasons, *separation_reasons))
        return AttestedEvaluation(
            base.attestation,
            False,
            False,
            base.contamination_free,
            reasons,
            base.evidence_digest,
        )


__all__ = ["AttestationTrustPolicy", "SeparationAwareAttestor"]
