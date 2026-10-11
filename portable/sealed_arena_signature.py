"""Ed25519 verification for externally signed sealed-Arena outcome receipts.

The evaluated runtime must not hold the private signing key. Configure only
trusted public keys in the verifier. Signatures cover the complete normalized
receipt digest and an explicit signer id; unkeyed receipt digests alone are not
authenticators. Requires the optional cryptography package.
"""
from __future__ import annotations

import base64
import json
import math
import time
from typing import Callable, Mapping, Iterable

from .sealed_arena_boundary import ExternalOutcomeReceipt


def receipt_signing_message(receipt: ExternalOutcomeReceipt, signer_id: str) -> bytes:
    """Return canonical bytes the independent evaluator must sign."""
    if not isinstance(receipt, ExternalOutcomeReceipt):
        raise TypeError("ExternalOutcomeReceipt is required")
    if not isinstance(signer_id, str) or not signer_id.strip():
        raise ValueError("signer_id must be a non-empty string")
    return json.dumps(
        {"schema": "auren-sealed-outcome-ed25519-v1",
         "signer_id": signer_id,
         "receipt_digest": receipt.receipt_digest},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def encode_receipt_signature(signer_id: str, signature: bytes) -> str:
    """Encode a detached Ed25519 signature as key-id.base64url."""
    if not isinstance(signer_id, str) or not signer_id.strip() or "." in signer_id:
        raise ValueError("signer_id must be non-empty and cannot contain a dot")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Ed25519 signatures must be exactly 64 bytes")
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{signer_id}.{encoded}"


class Ed25519ReceiptVerifier:
    """Fail-closed verifier with explicit key rotation, revocation, and validity.

    Trusted key IDs should be versioned (for example, "lab-2026q4") during
    rotation. Revocation and validity windows are deployment-owned trust inputs,
    not self-asserted receipt fields. An absent validity window means that the
    trusted key has no time limit configured.
    """

    def __init__(
        self,
        trusted_public_keys: Mapping[str, bytes | str],
        *,
        revoked_signers: Iterable[str] = (),
        key_validity: Mapping[str, tuple[float, float | None]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(trusted_public_keys, Mapping) or not trusted_public_keys:
            raise ValueError("a non-empty trusted Ed25519 public-key map is required")
        if not callable(clock):
            raise TypeError("clock must be callable")
        keys: dict[str, bytes] = {}
        seen: set[bytes] = set()
        for signer_id, encoded in trusted_public_keys.items():
            if not isinstance(signer_id, str) or not signer_id.strip() or "." in signer_id:
                raise ValueError("trusted signer IDs must be non-empty strings without dots")
            if isinstance(encoded, str):
                try:
                    key = bytes.fromhex(encoded)
                except ValueError as exc:
                    raise ValueError("trusted public keys must be 32-byte values or hex") from exc
            elif isinstance(encoded, bytes):
                key = encoded
            else:
                raise ValueError("trusted public keys must be bytes or hex strings")
            if len(key) != 32:
                raise ValueError("Ed25519 public keys must be exactly 32 bytes")
            if key in seen:
                raise ValueError("one public key cannot represent multiple trusted signer IDs")
            seen.add(key)
            keys[signer_id] = key

        if isinstance(revoked_signers, (str, bytes)):
            raise ValueError("revoked_signers must be a collection of signer IDs")
        revoked = frozenset(revoked_signers)
        if any(not isinstance(item, str) or not item.strip() for item in revoked):
            raise ValueError("revoked signer IDs must be non-empty strings")
        if not revoked <= set(keys):
            raise ValueError("revoked signer IDs must exist in the trusted key map")

        validity: dict[str, tuple[float, float | None]] = {}
        if key_validity is not None:
            if not isinstance(key_validity, Mapping):
                raise ValueError("key_validity must map signer IDs to validity windows")
            if not set(key_validity) <= set(keys):
                raise ValueError("key validity may only be configured for trusted signers")
            for signer_id, window in key_validity.items():
                if not isinstance(window, (tuple, list)) or len(window) != 2:
                    raise ValueError("key validity windows must be (not_before, not_after) pairs")
                start, end = window
                if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
                    raise ValueError("key validity not_before must be a finite timestamp")
                if end is not None and (
                    isinstance(end, bool) or not isinstance(end, (int, float))
                    or not math.isfinite(end) or end <= start
                ):
                    raise ValueError("key validity not_after must be finite and later than not_before")
                validity[signer_id] = (float(start), float(end) if end is not None else None)

        self._trusted_public_keys = keys
        self._revoked_signers = revoked
        self._key_validity = validity
        self._clock = clock

    def _signer_is_active(self, signer_id: str) -> bool:
        if signer_id not in self._trusted_public_keys or signer_id in self._revoked_signers:
            return False
        window = self._key_validity.get(signer_id)
        if window is None:
            return True
        try:
            now = self._clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
                return False
            not_before, not_after = window
            return now >= not_before and (not_after is None or now < not_after)
        except Exception:
            return False

    def verify_detached(self, signer_id: str, encoded_signature: str, message: bytes) -> bool:
        """Verify an unpadded base64url Ed25519 signature over exact message bytes."""
        try:
            if not self._signer_is_active(signer_id):
                return False
            if not isinstance(encoded_signature, str) or not encoded_signature or "=" in encoded_signature:
                return False
            signature = base64.urlsafe_b64decode(
                encoded_signature + "=" * ((4 - len(encoded_signature) % 4) % 4)
            )
            if len(signature) != 64 or base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=") != encoded_signature:
                return False
            if not isinstance(message, bytes):
                return False
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(self._trusted_public_keys[signer_id]).verify(signature, message)
            return True
        except Exception:
            return False

    def __call__(self, receipt: ExternalOutcomeReceipt) -> bool:
        if not isinstance(receipt, ExternalOutcomeReceipt):
            return False
        try:
            signer_id, encoded_signature = receipt.signature.split(".", 1)
            if not self._signer_is_active(signer_id):
                return False
            if signer_id in {receipt.evaluator_id, receipt.oracle_id}:
                return False
            return self.verify_detached(
                signer_id, encoded_signature, receipt_signing_message(receipt, signer_id)
            )
        except Exception:
            return False


__all__ = ["Ed25519ReceiptVerifier", "receipt_signing_message", "encode_receipt_signature"]
