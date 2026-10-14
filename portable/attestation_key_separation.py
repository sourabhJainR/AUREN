"""Explicit principal/key-separation policy for external Arena attestations.

Cryptographic verification alone is insufficient if the same principal can act
as evaluator, oracle, and attestation signer. Principal identities must also be
authenticated by a separately configured verifier; names alone are not proof.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, FrozenSet

from .independent_evaluation_attestation import (
    _PRODUCTION_EVALUATION_SEAL,
    _TRUSTED_EVALUATION_SEAL,
    AttestedEvaluation,
    EvaluationAttestation,
    IndependentEvaluationAttestor,
)


@dataclass(frozen=True, slots=True)
class AttestationTrustPolicy:
    trusted_signers: FrozenSet[str]
    require_distinct_signer: bool = True
    require_distinct_evaluator_oracle: bool = True
    trusted_key_ids: FrozenSet[str] = frozenset()
    revoked_signers: FrozenSet[str] = frozenset()
    revoked_key_ids: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        for name, values in (
            ("trusted_signers", self.trusted_signers),
            ("trusted_key_ids", self.trusted_key_ids),
            ("revoked_signers", self.revoked_signers),
            ("revoked_key_ids", self.revoked_key_ids),
        ):
            if not isinstance(values, frozenset) or (name in {"trusted_signers", "trusted_key_ids"} and not values):
                raise ValueError(f"{name} must be a non-empty frozenset" if name in {"trusted_signers", "trusted_key_ids"} else f"{name} must be a frozenset")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} entries must be non-empty strings")
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
        if attestation.signer_id in self.revoked_signers:
            reasons.append("attestation signer is revoked")
        if not attestation.key_id:
            reasons.append("attestation key id is required")
        elif attestation.key_id not in self.trusted_key_ids:
            reasons.append("attestation key id is not trusted")
        if attestation.key_id in self.revoked_key_ids:
            reasons.append("attestation key is revoked")
        if self.require_distinct_signer and attestation.signer_id in {evaluator_id, oracle_id}:
            reasons.append("attestation signer must be distinct from evaluator and oracle")
        return not reasons, tuple(reasons)


class SeparationAwareAttestor:
    """Verify signature, lineage, trusted signer, and authenticated principal separation."""

    def __init__(
        self,
        verify_signature: Callable[[EvaluationAttestation], bool],
        policy: AttestationTrustPolicy,
        verify_principal: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not isinstance(policy, AttestationTrustPolicy):
            raise TypeError("AttestationTrustPolicy is required")
        if verify_principal is not None and not callable(verify_principal):
            raise TypeError("verify_principal must be callable")
        self._attestor = IndependentEvaluationAttestor(verify_signature)
        self.policy = policy
        self.verify_principal = verify_principal

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
        reasons = list((*base.reasons, *separation_reasons))
        evaluator_authenticated = False
        oracle_authenticated = False
        if self.verify_principal is None:
            reasons.append("principal identity verifier is not configured")
        else:
            try:
                evaluator_authenticated = self.verify_principal(evaluator_id, "evaluator") is True
            except Exception:
                evaluator_authenticated = False
            try:
                oracle_authenticated = self.verify_principal(oracle_id, "oracle") is True
            except Exception:
                oracle_authenticated = False
            if not evaluator_authenticated:
                reasons.append("evaluator principal identity is not authenticated")
            if not oracle_authenticated:
                reasons.append("oracle principal identity is not authenticated")

        independent = separated and evaluator_authenticated and oracle_authenticated
        payload = {
            "base_evidence_digest": base.evidence_digest,
            "evaluator_id": evaluator_id,
            "oracle_id": oracle_id,
            "trusted_signers": sorted(self.policy.trusted_signers),
            "trusted_key_ids": sorted(self.policy.trusted_key_ids),
            "revoked_signers": sorted(self.policy.revoked_signers),
            "revoked_key_ids": sorted(self.policy.revoked_key_ids),
            "require_distinct_signer": self.policy.require_distinct_signer,
            "require_distinct_evaluator_oracle": self.policy.require_distinct_evaluator_oracle,
            "separation_passed": separated,
            "evaluator_authenticated": evaluator_authenticated,
            "oracle_authenticated": oracle_authenticated,
            "separation_reasons": tuple(separation_reasons),
        }
        evidence_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = AttestedEvaluation(
            attestation=base.attestation,
            verified=base.verified,
            independent=independent,
            contamination_free=base.contamination_free,
            reasons=tuple(reasons),
            evidence_digest=evidence_digest,
        )
        if result.verified and result.independent and result.contamination_free:
            object.__setattr__(result, "_trust_seal", _TRUSTED_EVALUATION_SEAL)
        return result


__all__ = ["AttestationTrustPolicy", "SeparationAwareAttestor"]
