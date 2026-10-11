"""Ed25519 verification for externally signed sealed-Arena outcome receipts.

The evaluated runtime must not hold the private signing key. Configure only
trusted public keys in the verifier. Signatures cover the complete normalized
receipt digest and an explicit signer id; unkeyed receipt digests alone are not
authenticators. Requires the optional cryptography package.
"""
from __future__ import annotations

import base64
import json
from typing import Mapping

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
    """Callable fail-closed verifier backed by an explicit trusted-key allowlist.

    trusted_public_keys maps signer IDs to 32-byte Ed25519 public keys.
    Values may be raw bytes or 64-character hexadecimal strings. Signature
    strings use signer-id.base64url-signature. Unknown signers, malformed
    keys/signatures, verifier failures, and evaluator/oracle signer collisions
    return False rather than trusting self-asserted identity.
    """

    def __init__(self, trusted_public_keys: Mapping[str, bytes | str]) -> None:
        if not isinstance(trusted_public_keys, Mapping) or not trusted_public_keys:
            raise ValueError("a non-empty trusted Ed25519 public-key map is required")
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
        self._trusted_public_keys = keys

    def verify_detached(self, signer_id: str, encoded_signature: str, message: bytes) -> bool:
        """Verify an unpadded base64url Ed25519 signature over exact message bytes."""
        try:
            if not isinstance(signer_id, str) or signer_id not in self._trusted_public_keys:
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
            if not signer_id or signer_id not in self._trusted_public_keys:
                return False
            if signer_id in {receipt.evaluator_id, receipt.oracle_id}:
                return False
            return self.verify_detached(
                signer_id, encoded_signature, receipt_signing_message(receipt, signer_id)
            )
        except Exception:
            return False



__all__ = ["Ed25519ReceiptVerifier", "receipt_signing_message", "encode_receipt_signature"]
