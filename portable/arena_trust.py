"""Production Ed25519 verification for externally signed Arena evidence.

Public keys and policy are injected from operator-controlled configuration. Private
keys must never be loaded by AUREN or the evaluated policy process. This module
authenticates signed claims; it does not independently prove the truth of those
claims or that the signer operates independently.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Mapping


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class Ed25519TrustRegistry:
    SCHEMA_VERSION = "auren-ed25519-trust-registry-v1"

    def __init__(self, *, public_keys: Mapping[str, str], validity: Mapping[str, Mapping[str, str]], revoked_signers: set[str] | frozenset[str] = frozenset(), role_assignments: Mapping[str, str] | None = None) -> None:
        self._keys = dict(public_keys)
        self._validity = {key: dict(value) for key, value in validity.items()}
        self._revoked = frozenset(revoked_signers)
        self._roles = dict(role_assignments or {})
        if set(self._keys) != set(self._validity):
            raise ValueError("every trusted signer must have a validity window and vice versa")
        if any(not isinstance(k, str) or not k.strip() for k in self._keys):
            raise ValueError("signer IDs must be non-empty")
        if any(not isinstance(v, str) or len(v) != 64 for v in self._keys.values()):
            raise ValueError("Ed25519 public keys must be 32-byte lowercase/uppercase hex")
        if any(not isinstance(w.get("not_before"), str) or not isinstance(w.get("not_after"), str) for w in self._validity.values()):
            raise ValueError("each key requires not_before and not_after timestamps")
        if len(set(self._roles.values())) != len(self._roles.values()):
            raise ValueError("operator, evaluator, oracle, and policy roles must have distinct signer IDs")

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("key validity timestamps must include a timezone")
        return parsed.astimezone(timezone.utc)

    def verify(self, *, signer_id: str, payload: Mapping[str, Any], signature_b64: str, now: datetime | None = None) -> bool:
        if signer_id not in self._keys or signer_id in self._revoked:
            return False
        if not isinstance(payload, Mapping) or not isinstance(signature_b64, str):
            return False
        try:
            current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            window = self._validity[signer_id]
            if current < self._parse_time(window["not_before"]) or current > self._parse_time(window["not_after"]):
                return False
            public_bytes = bytes.fromhex(self._keys[signer_id])
            signature = base64.b64decode(signature_b64, validate=True)
            if len(public_bytes) != 32 or len(signature) != 64:
                return False
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical_bytes(payload))
            return True
        except (ValueError, TypeError, KeyError, ImportError, Exception):
            return False

    def verify_attestation(self, attestation: Mapping[str, Any], *, expected_kind: str, expected_binding: str, now: datetime | None = None) -> bool:
        if not isinstance(attestation, Mapping) or set(attestation) != {"schema_version", "kind", "binding_digest", "signer_id", "issued_at", "payload", "signature"}:
            return False
        if attestation.get("schema_version") != self.SCHEMA_VERSION or attestation.get("kind") != expected_kind:
            return False
        if attestation.get("binding_digest") != expected_binding:
            return False
        payload = {key: attestation[key] for key in ("schema_version", "kind", "binding_digest", "signer_id", "issued_at", "payload")}
        return self.verify(signer_id=attestation.get("signer_id"), payload=payload, signature_b64=attestation.get("signature"), now=now)


__all__ = ["Ed25519TrustRegistry", "canonical_bytes"]
