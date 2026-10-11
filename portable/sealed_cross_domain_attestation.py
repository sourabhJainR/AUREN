"""Cryptographic verification for held-out target-domain provenance.

A target-domain operator signs the exact provenance claims and no-overlap
attestation digest. This proves integrity and signer identity, not real-world
organizational independence; trusted keys must be provisioned out of band.
"""
from __future__ import annotations

import json
from typing import Mapping

from .sealed_arena_signature import Ed25519ReceiptVerifier
from .sealed_cross_domain_arena import TargetDomainProvenance


def target_domain_signing_message(provenance: TargetDomainProvenance) -> bytes:
    """Canonical message signed by the named independent target-domain operator."""
    if not isinstance(provenance, TargetDomainProvenance):
        raise TypeError("TargetDomainProvenance is required")
    payload = {
        "schema": "auren-target-domain-provenance-ed25519-v1",
        "domain_id": provenance.domain_id,
        "origin": provenance.origin,
        "manifest_digest": provenance.manifest_digest,
        "split": provenance.split,
        "development_corpus_digest": provenance.development_corpus_digest,
        "development_overlap_status": provenance.development_overlap_status,
        "overlap_attestation_digest": provenance.overlap_attestation_digest,
        "independent_operator_id": provenance.independent_operator_id,
        "attestation_digest": provenance.attestation_digest,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class TargetDomainProvenanceVerifier:
    """Fail-closed verifier using an explicit Ed25519 trusted-key allowlist."""

    def __init__(self, trusted_public_keys: Mapping[str, bytes | str]) -> None:
        self._verifier = Ed25519ReceiptVerifier(trusted_public_keys)

    def __call__(self, provenance: TargetDomainProvenance) -> bool:
        if not isinstance(provenance, TargetDomainProvenance):
            return False
        signer = provenance.attestation_signer_id
        if not signer or signer != provenance.independent_operator_id:
            return False
        try:
            signature_signer, separator, encoded = provenance.attestation_signature.partition(".")
            if not separator or signature_signer != signer or not encoded:
                return False
            return self._verifier.verify_detached(
                signer, encoded, target_domain_signing_message(provenance)
            )
        except Exception:
            return False


__all__ = ["TargetDomainProvenanceVerifier", "target_domain_signing_message"]
