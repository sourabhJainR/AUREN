"""Signed custody attestations for independently controlled Arena holdouts.

An operator signature authenticates provenance metadata; it does not by itself
prove physical or organizational custody. Deployments must provision the
operator's public key out of band and keep its private key outside AUREN and
the evaluated runtime.
"""
from __future__ import annotations

import json
from typing import Mapping, Callable

from .sealed_corpus_provenance import SealedCorpusProvenance


def provenance_signing_message(provenance: SealedCorpusProvenance) -> bytes:
    """Canonical bytes the named independent corpus operator signs."""
    if not isinstance(provenance, SealedCorpusProvenance):
        raise TypeError("SealedCorpusProvenance is required")
    payload = {
        "schema": "auren-sealed-corpus-custody-ed25519-v1",
        "corpus_digest": provenance.corpus_digest,
        "manifest_digest": provenance.manifest_digest,
        "origin": provenance.origin,
        "split": provenance.split,
        "contamination_status": provenance.contamination_status,
        "independent_operator_id": provenance.independent_operator_id,
        "attestation_digest": provenance.attestation_digest,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def verify_custody_attestation(
    provenance: SealedCorpusProvenance,
    verify_detached_signature: Callable[[str, str, bytes], bool],
    *,
    evaluator_id: str,
    oracle_id: str,
) -> None:
    """Raise unless the exact holdout provenance is signed by its named operator."""
    if not callable(verify_detached_signature):
        raise ValueError("trusted custody signature verifier is required")
    signer = provenance.attestation_signer_id
    if not signer or not provenance.attestation_signature:
        raise ValueError("sealed holdout is missing its independent operator signature")
    if signer != provenance.independent_operator_id:
        raise ValueError("custody signature signer does not match the named independent operator")
    if signer in {evaluator_id, oracle_id}:
        raise ValueError("custody operator must be independent of evaluator and oracle")
    if not verify_detached_signature(signer, provenance.attestation_signature, provenance_signing_message(provenance)):
        raise ValueError("sealed holdout custody signature is not verified")


__all__ = ["provenance_signing_message", "verify_custody_attestation"]
