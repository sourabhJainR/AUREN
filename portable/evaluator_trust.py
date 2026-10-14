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
from .independent_evaluation_attestation import (
    _PRODUCTION_EVALUATION_SEAL,
    AttestedEvaluation,
    EvaluationAttestation,
)


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
        revoked_signers: Iterable[str] = (),
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
        if isinstance(revoked_signers, (str, bytes)):
            raise ValueError("revoked_signers must be a collection of signer IDs")
        revoked = frozenset(revoked_signers)
        if any(not isinstance(value, str) or not value.strip() for value in revoked):
            raise ValueError("revoked signer IDs must be non-empty strings")
        if not revoked <= {record.signer_id for record in records}:
            raise ValueError("revoked signer IDs must exist in the key registry")
        self._keys = by_key
        self._revoked_signers = revoked
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
        return self._revoked_signers

    @property
    def revoked_key_ids(self) -> frozenset[str]:
        return frozenset(key_id for key_id, record in self._keys.items() if record.revoked)

    def __call__(self, attestation: EvaluationAttestation) -> bool:
        if not isinstance(attestation, EvaluationAttestation):
            return False
        try:
            record = self._keys.get(attestation.key_id)
            if record is None or record.revoked or record.signer_id != attestation.signer_id or attestation.signer_id in self._revoked_signers:
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


class PinnedPrincipalVerifier:
    """Deployment-bound principal verifier wrapper; rejects anonymous/test callbacks."""

    production_ready = True

    def __init__(
        self,
        verify_principal: Callable[[str, str], bool],
        *,
        provider_id: str,
        provider_version: str,
        trust_source_ref: str,
        configuration_digest: str,
    ) -> None:
        if not callable(verify_principal):
            raise TypeError("verify_principal must be a callable backed by an authenticated identity provider")
        for name, value in (
            ("provider_id", provider_id),
            ("provider_version", provider_version),
            ("trust_source_ref", trust_source_ref),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty and pinned")
            if name != "trust_source_ref" and value.strip().lower() in {
                "test", "mock", "stub", "fake", "protocol", "unversioned", "latest"
            }:
                raise ValueError(f"{name} cannot identify a test or protocol-only verifier")
        if (
            not isinstance(configuration_digest, str)
            or len(configuration_digest) != 64
            or any(char not in "0123456789abcdef" for char in configuration_digest.lower())
        ):
            raise ValueError("configuration_digest must be a SHA-256 hex digest")
        self._verify_principal = verify_principal
        self.provider_id = provider_id.strip()
        self.provider_version = provider_version.strip()
        self.trust_source_ref = trust_source_ref.strip()
        self.configuration_digest = configuration_digest.lower()

    def __call__(self, principal: str, role: str) -> bool:
        try:
            return self._verify_principal(principal, role) is True
        except Exception:
            return False


class ProductionSeparationAwareAttestor:
    """Production-only attestor; refuses protocol/test signature and identity verifiers."""

    production_ready = True

    def __init__(
        self,
        keyring: Ed25519EvaluationAttestationVerifier,
        principal_verifier: PinnedPrincipalVerifier,
    ) -> None:
        if type(keyring) is not Ed25519EvaluationAttestationVerifier or not keyring.production_ready:
            raise TypeError("production attestation requires Ed25519EvaluationAttestationVerifier")
        if type(principal_verifier) is not PinnedPrincipalVerifier or not principal_verifier.production_ready:
            raise TypeError("production attestation requires PinnedPrincipalVerifier")
        if not keyring.signer_ids or not keyring.key_ids:
            raise ValueError("production evaluator trust roots must be configured")
        self.keyring = keyring
        self.principal_verifier = principal_verifier
        self.principal_verifier_id = principal_verifier.provider_id
        self.principal_verifier_version = principal_verifier.provider_version
        self._attestor = SeparationAwareAttestor(
            keyring,
            AttestationTrustPolicy(
                trusted_signers=keyring.signer_ids,
                trusted_key_ids=keyring.key_ids,
                revoked_signers=keyring.revoked_signers,
                revoked_key_ids=keyring.revoked_key_ids,
            ),
            principal_verifier,
        )

    def verify(self, attestation: EvaluationAttestation, **kwargs) -> AttestedEvaluation:
        result = self._attestor.verify(attestation, **kwargs)
        if result.trustworthy:
            object.__setattr__(result, "_production_seal", _PRODUCTION_EVALUATION_SEAL)
        return result


__all__ = [
    "EvaluatorKeyWindow",
    "Ed25519EvaluationAttestationVerifier",
    "PinnedPrincipalVerifier",
    "ProductionSeparationAwareAttestor",
    "evaluation_attestation_signing_message",
    "encode_evaluation_signature",
]
