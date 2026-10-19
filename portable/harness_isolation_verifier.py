"""Bridge signed Arena isolation attestations into Harness experiment receipts.

The adapter requires out-of-band signer/key policy plus a cryptographic signature
verifier, and a trusted runner-context resolver for image, host identity, and time
window. Values copied only from the probe's own receipt are not trusted context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .arena_isolation_attestation import ArenaIsolationAttestation, verify_arena_isolation_attestation
from .harness_experiments import HarnessExperimentManifest, canonical_digest


@dataclass(frozen=True, slots=True)
class TrustedHarnessRunContext:
    """Out-of-band facts supplied by the trusted sandbox launcher/operator."""
    expected_image_digest: str
    expected_sandbox_identity: str
    campaign_started_at_epoch: float
    campaign_ended_at_epoch: float


class ArenaHarnessIsolationReceiptVerifier:
    """Validate a harness receipt against a signed Arena report.

    Production use requires a real detached-signature verifier backed by provisioned
    public keys and a resolver backed by the trusted runner/control plane. Test fakes
    do not establish real isolation.
    """

    def __init__(
        self,
        *,
        verify_detached_signature: Callable[[str, str, bytes], bool],
        resolve_trusted_context: Callable[[Mapping[str, Any], HarnessExperimentManifest, str], TrustedHarnessRunContext],
        trusted_signer_ids: frozenset[str],
        revoked_signer_ids: frozenset[str],
        trusted_signer_key_ids: frozenset[str],
        revoked_signer_key_ids: frozenset[str],
        evaluator_id: str,
        oracle_id: str,
        policy_runtime_id: str,
        clock: Callable[[], float],
        max_future_skew_seconds: float = 30.0,
    ) -> None:
        if not callable(verify_detached_signature) or not callable(resolve_trusted_context):
            raise ValueError("trusted signature verifier and runner-context resolver are required")
        for name, values, allow_empty in (
            ("trusted_signer_ids", trusted_signer_ids, False),
            ("trusted_signer_key_ids", trusted_signer_key_ids, False),
            ("revoked_signer_ids", revoked_signer_ids, True),
            ("revoked_signer_key_ids", revoked_signer_key_ids, True),
        ):
            if not isinstance(values, frozenset) or (not allow_empty and not values):
                raise ValueError(f"{name} must be an out-of-band frozenset")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} entries must be non-empty strings")
        for name, value in (("evaluator_id", evaluator_id), ("oracle_id", oracle_id),
                            ("policy_runtime_id", policy_runtime_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if isinstance(max_future_skew_seconds, bool) or not isinstance(max_future_skew_seconds, (int, float)) or max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be non-negative")
        self._verify_signature = verify_detached_signature
        self._resolve_context = resolve_trusted_context
        self._trusted_signers = trusted_signer_ids
        self._revoked_signers = revoked_signer_ids
        self._trusted_key_ids = trusted_signer_key_ids
        self._revoked_key_ids = revoked_signer_key_ids
        self._evaluator_id = evaluator_id
        self._oracle_id = oracle_id
        self._policy_runtime_id = policy_runtime_id
        self._clock = clock
        self._max_future_skew_seconds = float(max_future_skew_seconds)

    def __call__(self, receipt: Mapping[str, Any], manifest: HarnessExperimentManifest, harness_digest: str) -> bool:
        try:
            return self.verify(receipt, manifest, harness_digest)
        except Exception:
            # Fail closed; worker-controlled receipts do not receive trusted error details.
            return False

    def verify(self, receipt: Mapping[str, Any], manifest: HarnessExperimentManifest, harness_digest: str) -> bool:
        if not isinstance(receipt, Mapping) or not isinstance(manifest, HarnessExperimentManifest):
            return False
        if not isinstance(harness_digest, str) or not harness_digest:
            return False
        if (
            receipt.get("schema_version") != "auren-isolation-receipt-v1"
            or receipt.get("manifest_digest") != manifest.digest
            or receipt.get("task_digest") != manifest.task_digest
            or receipt.get("harness_digest") != harness_digest
            or receipt.get("isolation_profile") != manifest.isolation_profile
            or receipt.get("isolation_enforced") is not True
            or receipt.get("egress_restricted") is not True
            or receipt.get("secrets_absent") is not True
        ):
            return False

        run_receipt = receipt.get("run_receipt")
        raw_attestation = receipt.get("attestation")
        if not isinstance(run_receipt, Mapping) or not isinstance(raw_attestation, Mapping):
            return False
        required_fields = {
            "schema_version", "run_id", "manifest_digest", "task_digest", "harness_digest",
            "isolation_profile", "workspace_digest", "image_digest", "sandbox_id", "stage",
            "environment_digest", "policy_digest", "controls",
        }
        if set(run_receipt) != required_fields:
            return False
        if (
            run_receipt.get("schema_version") != "auren-harness-run-receipt-v1"
            or run_receipt.get("run_id") != manifest.run_id
            or run_receipt.get("manifest_digest") != manifest.digest
            or run_receipt.get("task_digest") != manifest.task_digest
            or run_receipt.get("harness_digest") != harness_digest
            or run_receipt.get("isolation_profile") != manifest.isolation_profile
            or run_receipt.get("environment_digest") != manifest.environment_digest
            or run_receipt.get("policy_digest") != manifest.policy_digest
            or receipt.get("workspace_digest") != run_receipt.get("workspace_digest")
            or not isinstance(run_receipt.get("workspace_digest"), str)
            or len(run_receipt["workspace_digest"]) != 64
            or not isinstance(run_receipt.get("controls"), list)
            or any(not isinstance(control, str) or not control.strip() for control in run_receipt["controls"])
        ):
            return False
        for key in required_fields - {"controls"}:
            if not isinstance(run_receipt.get(key), str) or not run_receipt[key].strip():
                return False

        try:
            attestation = ArenaIsolationAttestation(
                campaign_digest=raw_attestation["campaign_digest"],
                run_receipt_digest=raw_attestation["run_receipt_digest"],
                image_digest=raw_attestation["image_digest"],
                sandbox_id=raw_attestation["sandbox_id"],
                signer_id=raw_attestation["signer_id"],
                signature=raw_attestation["signature"],
                issued_at_epoch=raw_attestation["issued_at_epoch"],
                expires_at_epoch=raw_attestation["expires_at_epoch"],
                controls=tuple(raw_attestation["controls"]),
                observed_host_identity=raw_attestation["observed_host_identity"],
                observation_started_at_epoch=raw_attestation["observation_started_at_epoch"],
                observation_ended_at_epoch=raw_attestation["observation_ended_at_epoch"],
                signer_key_id=raw_attestation["signer_key_id"],
            )
        except (KeyError, TypeError, ValueError):
            return False

        if (
            run_receipt["image_digest"] != attestation.image_digest
            or run_receipt["sandbox_id"] != attestation.sandbox_id
            or run_receipt["stage"] != receipt.get("stage")
            or tuple(run_receipt["controls"]) != attestation.controls
        ):
            return False

        trusted = self._resolve_context(receipt, manifest, harness_digest)
        if not isinstance(trusted, TrustedHarnessRunContext):
            return False
        if run_receipt["image_digest"] != trusted.expected_image_digest:
            return False
        expected_run_digest = canonical_digest(dict(run_receipt))
        verified_digest = verify_arena_isolation_attestation(
            attestation,
            self._verify_signature,
            expected_campaign_digest=manifest.digest,
            expected_run_receipt_digest=expected_run_digest,
            expected_image_digest=trusted.expected_image_digest,
            evaluator_id=self._evaluator_id,
            oracle_id=self._oracle_id,
            policy_runtime_id=self._policy_runtime_id,
            trusted_signer_ids=self._trusted_signers,
            now=self._clock(),
            expected_sandbox_identity=trusted.expected_sandbox_identity,
            revoked_signer_ids=self._revoked_signers,
            trusted_signer_key_ids=self._trusted_key_ids,
            revoked_signer_key_ids=self._revoked_key_ids,
            campaign_started_at_epoch=trusted.campaign_started_at_epoch,
            campaign_ended_at_epoch=trusted.campaign_ended_at_epoch,
            max_future_skew_seconds=self._max_future_skew_seconds,
        )
        return isinstance(verified_digest, str) and len(verified_digest) == 64 and attestation.attestation_digest == verified_digest


__all__ = ["ArenaHarnessIsolationReceiptVerifier", "TrustedHarnessRunContext"]
