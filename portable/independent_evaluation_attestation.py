"""Independent evaluation attestation boundary.

The runtime may consume an externally signed evaluation receipt, but cannot
manufacture trust locally. Signature verification is injected so deployments
can use their own PKI/attestation substrate without a hard dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib, json
from typing import Callable


@dataclass(frozen=True, slots=True)
class EvaluationAttestation:
    campaign_digest: str
    corpus_digest: str
    oracle_digest: str
    evaluator_version: str
    signer_id: str
    signature: str

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_digest", self.campaign_digest),
            ("corpus_digest", self.corpus_digest),
            ("oracle_digest", self.oracle_digest),
            ("evaluator_version", self.evaluator_version),
            ("signer_id", self.signer_id),
            ("signature", self.signature),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} is required")

    @property
    def attestation_digest(self) -> str:
        payload={
            "campaign_digest":self.campaign_digest,
            "corpus_digest":self.corpus_digest,
            "oracle_digest":self.oracle_digest,
            "evaluator_version":self.evaluator_version,
            "signer_id":self.signer_id,
        }
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AttestedEvaluation:
    attestation: EvaluationAttestation
    verified: bool
    independent: bool
    contamination_free: bool
    reasons: tuple[str, ...]
    evidence_digest: str

    @property
    def trustworthy(self) -> bool:
        return self.verified and self.independent and self.contamination_free


class IndependentEvaluationAttestor:
    """Verify an external attestation and bind it to expected evaluation lineage."""

    def __init__(self, verify_signature: Callable[[EvaluationAttestation], bool]) -> None:
        if not callable(verify_signature):
            raise TypeError("verify_signature must be callable")
        self.verify_signature=verify_signature

    def verify(
        self,
        attestation: EvaluationAttestation,
        *,
        expected_campaign_digest: str,
        expected_corpus_digest: str,
        expected_oracle_digest: str,
        contamination_detected: bool = False,
    ) -> AttestedEvaluation:
        if not expected_campaign_digest.strip() or not expected_corpus_digest.strip() or not expected_oracle_digest.strip():
            raise ValueError("expected evaluation lineage is required")
        reasons=[]
        lineage_ok=(
            attestation.campaign_digest == expected_campaign_digest
            and attestation.corpus_digest == expected_corpus_digest
            and attestation.oracle_digest == expected_oracle_digest
        )
        if not lineage_ok:
            reasons.append("attestation lineage does not match expected campaign/corpus/oracle")
        try:
            signature_ok=bool(self.verify_signature(attestation))
        except Exception as exc:
            signature_ok=False
            reasons.append(f"signature verification failed: {exc}")
        if not signature_ok:
            reasons.append("external signature is not verified")
        if contamination_detected:
            reasons.append("evaluation reports contamination")
        payload={
            "attestation_digest":attestation.attestation_digest,
            "lineage_ok":lineage_ok,
            "signature_ok":signature_ok,
            "contamination_free":not contamination_detected,
            "signer_id":attestation.signer_id,
        }
        evidence_digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return AttestedEvaluation(
            attestation,
            lineage_ok and signature_ok,
            True,
            not contamination_detected,
            tuple(reasons),
            evidence_digest,
        )


__all__=["EvaluationAttestation","AttestedEvaluation","IndependentEvaluationAttestor"]
