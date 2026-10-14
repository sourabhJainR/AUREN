"""Production trust boundary for independently signed evaluator attestations.

Keys are operator-managed public keys only. The evaluator private key must remain
outside AUREN. Key IDs are versioned, validity windows are mandatory, and
revocation is checked at verification time.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Callable, Iterable, Mapping

from .attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from .independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation


def evaluation_attestation_signing_message(attestation: EvaluationAttestation) -> bytes:
    """Canonical Ed25519 message; signature bytes are not part of the digest."""
    if not isinstance(attestation, EvaluationAttestation):
        raise TypeError("EvaluationAttestation is required")
    return json.dumps(
        {
            "schema": "auren-independent-evaluation-ed25519-v1",
            "attestation_digest": attestation.attestation_digest,
            "signer_id": attestation.signer_id,
            "key_id": attestation.key_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def encode_evaluation_signature(signature: bytes) -> str:
    """Encode a detached Ed25519 signature as canonical unpadded base64url."""
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Ed25519 signatures must be exactly 64 bytes")
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class EvaluatorKeyWindow:
    key_id: str
    signer_id: str
    public_key: bytes | str
    not_before_epoch: float
    not_after_epoch: float
    revoked: bool = False

    def __post_init__(self) -> None:
        for name in ("key_id", "signer_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.key_id.strip().lower() in {"test", "mock", "unversioned", "latest"}:
            raise ValueError("key_id must be a pinned versioned identifier")
        if isinstance(self.public_key, bytes):
            key_bytes = self.public_key
        elif isinstance(self.public_key, str):
            try:
                key_bytes = bytes.fromhex(self.public_key)
            except ValueError as exc:
                raise ValueError("public_key must be 32 raw bytes or a 64-character hex string") from exc
        else:
            raise ValueError("public_key must be bytes or hex")
        if len(key_bytes) != 32:
            raise ValueError("Ed25519 public keys must be exactly 32 bytes")
        for name in ("not_before_epoch", "not_after_epoch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite timestamp")
        if self.not_after_epoch <= self.not_before_epoch:
            raise ValueError("key validity window must have a positive duration")
        if type(self.revoked) is not bool:
            raise ValueError("revoked must be a boolean")
        object.__setattr__(self, "key_id", self.key_id.strip())
        object.__setattr__(self, "signer_id", self.signer_id.strip())
        object.__setattr__(self, "public_key", key_bytes)
        object.__setattr__(self, "not_before_epoch", float(self.not_before_epoch))
        object.__setattr__(self, "not_after_epoch", float(self.not_after_epoch))


class Ed25519EvaluationAttestationVerifier:
    """Concrete production verifier with key rotation, validity, and revocation."""

    production_ready = True

    def __init__(
        self,
        keys: Iterable[EvaluatorKeyWindow],
        *,
        clock: Callable[[], float] = time.time,
        max_future_skew_seconds: float = 30.0,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(max_future_skew_seconds, bool)
            or not isinstance(max_future_skew_seconds, (int, float))
            or not math.isfinite(max_future_skew_seconds)
            or max_future_skew_seconds < 0
        ):
            raise ValueError("max_future_skew_seconds must be finite and non-negative")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("production evaluator verification requires the cryptography package") from exc

        records = tuple(keys)
        if not records or any(not isinstance(item, EvaluatorKeyWindow) for item in records):
            raise ValueError("a non-empty collection of EvaluatorKeyWindow records is required")
        by_key: dict[str, EvaluatorKeyWindow] = {}
        seen_material: set[bytes] = set()
        for record in records:
            if record.key_id in by_key:
                raise ValueError(f"duplicate evaluator key id: {record.key_id}")
            public_key = record.public_key
            if public_key in seen_material:
                raise ValueError("one public key cannot be registered under multiple key IDs")
            seen_material.add(public_key)
            by_key[record.key_id] = record
        self._keys = by_key
        self._clock = clock
        self._max_future_skew_seconds = float(max_future_skew_seconds)

    @property
    def signer_ids(self) -> frozenset[str]:
        return frozenset(record.signer_id for record in self._keys.values())

    @property
    def key_ids(self) -> frozenset[str]:
        return frozenset(self._keys)

    @property
    def revoked_signers(self) -> frozenset[str]:
        return frozenset(record.signer_id for record in self._keys.values() if record.revoked)

    @property
    def revoked_key_ids(self) -> frozenset[str]:
        return frozenset(key_id for key_id, record in self._keys.items() if record.revoked)

    def __call__(self, attestation: EvaluationAttestation) -> bool:
        if not isinstance(attestation, EvaluationAttestation):
            return False
        try:
            record = self._keys.get(attestation.key_id)
            if record is None or record.revoked or record.signer_id != attestation.signer_id:
                return False
            now = self._clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
                return False
            now = float(now)
            if now < record.not_before_epoch or now >= record.not_after_epoch:
                return False
            if attestation.issued_at_epoch <= 0 or attestation.expires_at_epoch <= 0:
                return False
            if attestation.issued_at_epoch > now + self._max_future_skew_seconds:
                return False
            if attestation.expires_at_epoch <= now:
                return False
            if attestation.issued_at_epoch < record.not_before_epoch:
                return False
            if attestation.expires_at_epoch > record.not_after_epoch:
                return False
            encoded = attestation.signature
            if not isinstance(encoded, str) or not encoded or "=" in encoded:
                return False
            signature = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
            if len(signature) != 64 or base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=") != encoded:
                return False
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(record.public_key).verify(
                signature, evaluation_attestation_signing_message(attestation)
            )
            return True
        except Exception:
            return False


class ProductionSeparationAwareAttestor:
    """Production-only attestor; refuses injected protocol/test signature verifiers."""

    production_ready = True

    def __init__(
        self,
        keyring: Ed25519EvaluationAttestationVerifier,
        verify_principal: Callable[[str, str], bool],
        *,
        principal_verifier_id: str,
        principal_verifier_version: str,
    ) -> None:
        if not isinstance(keyring, Ed25519EvaluationAttestationVerifier) or not keyring.production_ready:
            raise TypeError("production attestation requires Ed25519EvaluationAttestationVerifier")
        if not callable(verify_principal):
            raise TypeError("an authenticated principal verifier is required")
        for name, value in (
            ("principal_verifier_id", principal_verifier_id),
            ("principal_verifier_version", principal_verifier_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty and pinned")
            if value.strip().lower() in {"test", "mock", "stub", "fake", "protocol", "unversioned", "latest"}:
                raise ValueError(f"{name} cannot identify a test or protocol-only verifier")
        if not keyring.signer_ids or not keyring.key_ids:
            raise ValueError("production evaluator trust roots must be configured")
        self.keyring = keyring
        self.principal_verifier_id = principal_verifier_id.strip()
        self.principal_verifier_version = principal_verifier_version.strip()
        self._attestor = SeparationAwareAttestor(
            keyring,
            AttestationTrustPolicy(
                trusted_signers=keyring.signer_ids,
                trusted_key_ids=keyring.key_ids,
                revoked_signers=keyring.revoked_signers,
                revoked_key_ids=keyring.revoked_key_ids,
            ),
            verify_principal,
        )

    def verify(self, attestation: EvaluationAttestation, **kwargs) -> AttestedEvaluation:
        return self._attestor.verify(attestation, **kwargs)


__all__ = [
    "EvaluatorKeyWindow",
    "Ed25519EvaluationAttestationVerifier",
    "ProductionSeparationAwareAttestor",
    "evaluation_attestation_signing_message",
    "encode_evaluation_signature",
]
