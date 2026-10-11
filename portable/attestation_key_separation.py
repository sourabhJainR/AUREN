"""Explicit principal/key-separation policy for external Arena attestations.

Cryptographic verification alone is insufficient if the same principal can act
as evaluator, oracle, and attestation signer. This module binds the separation
decision into the evidence digest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, FrozenSet

from .independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation, IndependentEvaluationAttestor


@dataclass(frozen=True, slots=True)
class AttestationTrustPolicy:
    trusted_signers: FrozenSet[str]
    require_distinct_signer: bool = True
    require_distinct_evaluator_oracle: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.trusted_signers, frozenset) or not self.trusted_signers:
            raise ValueError("trusted_signers must be a non-empty frozenset")
        if any(not isinstance(signer, str) or not signer.strip() for signer in self.trusted_signers):
            raise ValueError("trusted signer ids must be non-empty strings")
        if type(self.require_distinct_signer) is not bool or type(self.require_distinct_evaluator_oracle) is not bool:
            raise ValueError("attestation separation policy flags must be booleans")

    def validate(self, attestation: EvaluationAttestation, *, evaluator_id: str, oracle_id: str) -> tuple[bool, tuple[str, ...]]:
        if not isinstance(evaluator_id, str) or not evaluator_id.strip() or not isinstance(oracle_id, str) or not oracle_id.strip():
            raise ValueError("evaluator_id and oracle_id are required non-empty strings")
        reasons: list[str] = []
        if self.require_distinct_evaluator_oracle and evaluator_id == oracle_id:
            reasons.append("evaluator and oracle principals must be distinct")
        if attestation.signer_id not in self.trusted_signers:
            reasons.append("attestation signer is not trusted")
        if self.require_distinct_signer and attestation.signer_id in {evaluator_id, oracle_id}:
            reasons.append("attestation signer must be distinct from evaluator and oracle")
        return not reasons, tuple(reasons)


class SeparationAwareAttestor:
    """Verify signature, lineage, trusted signer, and evaluator/oracle separation."""

    def __init__(self, verify_signature, policy: AttestationTrustPolicy, verify_principal_identity: Callable[[str, str], bool]) -> None:
        if not isinstance(policy, AttestationTrustPolicy):
            raise TypeError("AttestationTrustPolicy is required")
        if not callable(verify_principal_identity):
            raise TypeError("verify_principal_identity must be a trusted identity-registry verifier")
        self._attestor = IndependentEvaluationAttestor(verify_signature)
        self.policy = policy
        self.verify_principal_identity = verify_principal_identity

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
        separated, separation_reasons = self.policy.validate(
            attestation, evaluator_id=evaluator_id, oracle_id=oracle_id
        )
        # Principal labels supplied by the caller are not identity proof. A
        # deployment must validate both role bindings against its trusted
        # identity registry; exceptions and truthy non-bools fail closed.
        try:
            evaluator_identity_verified = self.verify_principal_identity(evaluator_id, "evaluator") is True
        except Exception:
            evaluator_identity_verified = False
        try:
            oracle_identity_verified = self.verify_principal_identity(oracle_id, "oracle") is True
        except Exception:
            oracle_identity_verified = False
        identity_reasons = []
        if not evaluator_identity_verified:
            identity_reasons.append("evaluator principal identity is not authenticated by trusted registry")
        if not oracle_identity_verified:
            identity_reasons.append("oracle principal identity is not authenticated by trusted registry")
        reasons = tuple((*base.reasons, *separation_reasons, *identity_reasons))
        payload = {
            "base_evidence_digest": base.evidence_digest,
            "evaluator_id": evaluator_id,
            "oracle_id": oracle_id,
            "trusted_signers": sorted(self.policy.trusted_signers),
            "require_distinct_signer": self.policy.require_distinct_signer,
            "require_distinct_evaluator_oracle": self.policy.require_distinct_evaluator_oracle,
            "separation_passed": separated,
            "separation_reasons": separation_reasons,
            "evaluator_identity_verified": evaluator_identity_verified,
            "oracle_identity_verified": oracle_identity_verified,
        }
        evidence_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return AttestedEvaluation(
            attestation=base.attestation,
            verified=base.verified,
            independent=separated and evaluator_identity_verified and oracle_identity_verified,
            contamination_free=base.contamination_free,
            reasons=reasons,
            evidence_digest=evidence_digest,
        )


__all__ = ["AttestationTrustPolicy", "SeparationAwareAttestor"]
