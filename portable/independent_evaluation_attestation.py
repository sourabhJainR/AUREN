"""Independent evaluation attestation boundary.

This base verifier checks signature and evaluation lineage only. Principal
independence is not established until a separate key-separation policy passes.
The runtime cannot manufacture trust locally; cryptographic verification is
injected so deployments can use their own PKI/attestation substrate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Callable


_TRUSTED_EVALUATION_SEAL = object()
_PRODUCTION_EVALUATION_SEAL = object()


def _required_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class EvaluationAttestation:
    campaign_digest: str
    corpus_digest: str
    oracle_digest: str
    evaluator_version: str
    signer_id: str
    signature: str
    key_id: str = ""
    run_id: str = ""
    receipt_digest: str = ""
    runtime_digest: str = ""
    issued_at_epoch: float = 0.0
    expires_at_epoch: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_digest", self.campaign_digest),
            ("corpus_digest", self.corpus_digest),
            ("oracle_digest", self.oracle_digest),
            ("evaluator_version", self.evaluator_version),
            ("signer_id", self.signer_id),
            ("signature", self.signature),
        ):
            _required_text(name, value)
        if not isinstance(self.key_id, str):
            raise ValueError("key_id must be a string")
        for name in ("run_id", "receipt_digest", "runtime_digest"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        for name in ("issued_at_epoch", "expires_at_epoch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative timestamp")
        if self.expires_at_epoch and self.issued_at_epoch and self.expires_at_epoch <= self.issued_at_epoch:
            raise ValueError("expires_at_epoch must be later than issued_at_epoch")

    @property
    def attestation_digest(self) -> str:
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "oracle_digest": self.oracle_digest,
            "evaluator_version": self.evaluator_version,
            "signer_id": self.signer_id,
            "key_id": self.key_id,
            "run_id": self.run_id,
            "receipt_digest": self.receipt_digest,
            "runtime_digest": self.runtime_digest,
            "issued_at_epoch": self.issued_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AttestedEvaluation:
    attestation: EvaluationAttestation
    verified: bool
    independent: bool
    contamination_free: bool
    reasons: tuple[str, ...]
    evidence_digest: str
    _trust_seal: object = field(default=None, init=False, repr=False, compare=False)
    _production_seal: object = field(default=None, init=False, repr=False, compare=False)

    @property
    def trustworthy(self) -> bool:
        return (
            self.verified and self.independent and self.contamination_free
            and self._trust_seal is _TRUSTED_EVALUATION_SEAL
        )

    @property
    def production_trustworthy(self) -> bool:
        return self.trustworthy and self._production_seal is _PRODUCTION_EVALUATION_SEAL


class IndependentEvaluationAttestor:
    """Verify signature and lineage; use SeparationAwareAttestor for independence."""

    def __init__(self, verify_signature: Callable[[EvaluationAttestation], bool]) -> None:
        if not callable(verify_signature):
            raise TypeError("verify_signature must be callable")
        self.verify_signature = verify_signature

    def verify(
        self,
        attestation: EvaluationAttestation,
        *,
        expected_campaign_digest: str,
        expected_corpus_digest: str,
        expected_oracle_digest: str,
        contamination_detected: bool = False,
    ) -> AttestedEvaluation:
        if not isinstance(attestation, EvaluationAttestation):
            raise TypeError("EvaluationAttestation is required")
        for name, value in (
            ("expected_campaign_digest", expected_campaign_digest),
            ("expected_corpus_digest", expected_corpus_digest),
            ("expected_oracle_digest", expected_oracle_digest),
        ):
            _required_text(name, value)
        if type(contamination_detected) is not bool:
            raise ValueError("contamination_detected must be a boolean")

        reasons: list[str] = []
        lineage_ok = (
            attestation.campaign_digest == expected_campaign_digest
            and attestation.corpus_digest == expected_corpus_digest
            and attestation.oracle_digest == expected_oracle_digest
        )
        if not lineage_ok:
            reasons.append("attestation lineage does not match expected campaign/corpus/oracle")
        try:
            signature_ok = self.verify_signature(attestation) is True
        except Exception:
            # Do not copy verifier exception text into a durable evidence record.
            signature_ok = False
        if not signature_ok:
            reasons.append("external signature is not verified")
        if contamination_detected:
            reasons.append("evaluation reports contamination")

        payload = {
            "attestation_digest": attestation.attestation_digest,
            "lineage_ok": lineage_ok,
            "signature_ok": signature_ok,
            "contamination_free": not contamination_detected,
            "signer_id": attestation.signer_id,
            "principal_independence": "not-established",
        }
        evidence_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return AttestedEvaluation(
            attestation=attestation,
            verified=lineage_ok and signature_ok,
            independent=False,
            contamination_free=not contamination_detected,
            reasons=tuple(reasons),
            evidence_digest=evidence_digest,
        )


__all__ = ["EvaluationAttestation", "AttestedEvaluation", "IndependentEvaluationAttestor"]
