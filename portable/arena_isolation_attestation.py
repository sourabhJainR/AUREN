"""Externally signed evidence that an Arena run's sandbox controls were observed.

A valid signature authenticates a report; it cannot independently prove the
operator's claims. Trust roots must be provisioned out of band and the report
must come from an independently audited sandbox operator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Callable

_REQUIRED_CONTROLS = frozenset({
    "network_disabled",
    "no_host_mounts",
    "read_only_rootfs",
    "non_root_execution",
    "credentials_not_exposed",
    "resource_limits_enforced",
    "termination_observed",
})
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ArenaIsolationAttestation:
    campaign_digest: str
    run_receipt_digest: str
    image_digest: str
    sandbox_id: str
    signer_id: str
    signature: str
    issued_at_epoch: float
    expires_at_epoch: float
    controls: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("campaign_digest", "run_receipt_digest", "image_digest", "sandbox_id", "signer_id", "signature"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not _IMAGE_DIGEST_RE.fullmatch(self.image_digest):
            raise ValueError("image_digest must be an immutable sha256 digest")
        for name in ("issued_at_epoch", "expires_at_epoch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive Unix timestamp")
        if self.issued_at_epoch >= self.expires_at_epoch:
            raise ValueError("isolation attestation expiry must follow issuance")
        if not isinstance(self.controls, tuple) or any(not isinstance(x, str) or not x.strip() for x in self.controls):
            raise ValueError("controls must be a tuple of non-empty control identifiers")
        if len(set(self.controls)) != len(self.controls):
            raise ValueError("controls must be unique")

    def signing_message(self) -> bytes:
        payload = {
            "schema": "auren-arena-isolation-attestation-v1",
            "campaign_digest": self.campaign_digest,
            "run_receipt_digest": self.run_receipt_digest,
            "image_digest": self.image_digest,
            "sandbox_id": self.sandbox_id,
            "signer_id": self.signer_id,
            "issued_at_epoch": self.issued_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
            "controls": self.controls,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

    @property
    def attestation_digest(self) -> str:
        return hashlib.sha256(self.signing_message() + b"\\0" + self.signature.encode("utf-8")).hexdigest()


def verify_arena_isolation_attestation(
    attestation: ArenaIsolationAttestation,
    verify_detached_signature: Callable[[str, str, bytes], bool],
    *,
    expected_campaign_digest: str,
    expected_run_receipt_digest: str,
    expected_image_digest: str,
    evaluator_id: str,
    oracle_id: str,
    policy_runtime_id: str,
    now: float,
    max_future_skew_seconds: float = 30.0,
) -> str:
    """Return the attestation digest only when the signed report is valid and bound."""
    if not isinstance(attestation, ArenaIsolationAttestation):
        raise TypeError("ArenaIsolationAttestation is required")
    if not callable(verify_detached_signature):
        raise ValueError("trusted isolation signature verifier is required")
    for name, value in (
        ("expected_campaign_digest", expected_campaign_digest),
        ("expected_run_receipt_digest", expected_run_receipt_digest),
        ("expected_image_digest", expected_image_digest),
        ("evaluator_id", evaluator_id),
        ("oracle_id", oracle_id),
        ("policy_runtime_id", policy_runtime_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not _IMAGE_DIGEST_RE.fullmatch(expected_image_digest):
        raise ValueError("expected_image_digest must be an immutable sha256 digest")
    if attestation.campaign_digest != expected_campaign_digest:
        raise ValueError("isolation attestation campaign digest mismatch")
    if attestation.run_receipt_digest != expected_run_receipt_digest:
        raise ValueError("isolation attestation run receipt digest mismatch")
    if attestation.image_digest != expected_image_digest:
        raise ValueError("isolation attestation image digest mismatch")
    if attestation.signer_id in {evaluator_id, oracle_id, policy_runtime_id}:
        raise ValueError("isolation attestation signer must be independent of evaluator, oracle, and policy runtime")
    missing = sorted(_REQUIRED_CONTROLS - set(attestation.controls))
    if missing:
        raise ValueError("isolation attestation is missing required controls: " + ", ".join(missing))
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now <= 0:
        raise ValueError("isolation verification clock must be a finite positive Unix timestamp")
    if isinstance(max_future_skew_seconds, bool) or not isinstance(max_future_skew_seconds, (int, float)) or not math.isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
        raise ValueError("max_future_skew_seconds must be finite and non-negative")
    if attestation.issued_at_epoch > now + max_future_skew_seconds:
        raise ValueError("isolation attestation is issued in the future")
    if attestation.expires_at_epoch <= now:
        raise ValueError("isolation attestation is stale or expired")
    if not verify_detached_signature(attestation.signer_id, attestation.signature, attestation.signing_message()):
        raise ValueError("isolation attestation signature is not verified")
    return attestation.attestation_digest


__all__ = ["ArenaIsolationAttestation", "verify_arena_isolation_attestation"]
