from __future__ import annotations

import hashlib
import math
import unittest

from portable.arena_isolation_attestation import ArenaIsolationAttestation
from portable.harness_experiments import HarnessExperimentManifest, canonical_digest
from portable.harness_isolation_verifier import (
    ArenaHarnessIsolationReceiptVerifier,
    TrustedHarnessRunContext,
)


CONTROLS = (
    "network_disabled", "no_host_mounts", "read_only_rootfs",
    "non_root_execution", "credentials_not_exposed", "resource_limits_enforced",
    "termination_observed",
)


class ArenaHarnessIsolationVerifierTests(unittest.TestCase):
    def setUp(self):
        self.now = 2000.0
        self.manifest = HarnessExperimentManifest(
            schema_version="auren-harness-experiment-v1",
            run_id="isolation-run-1",
            task_digest=canonical_digest({"task": "verify isolation"}),
            base_harness_digest="a" * 64,
            executor_id="sandbox-executor-v1",
            evaluator_id="independent-evaluator-v1",
            seed=5,
            max_candidates=2,
            max_depth=0,
            isolation_profile="restricted-profile-v1",
            policy_digest="b" * 64,
            environment_digest="c" * 64,
        )
        self.harness_digest = "d" * 64
        self.image_digest = "sha256:" + "e" * 64
        self.run_receipt = {
            "schema_version": "auren-harness-run-receipt-v1",
            "run_id": self.manifest.run_id,
            "manifest_digest": self.manifest.digest,
            "task_digest": self.manifest.task_digest,
            "harness_digest": self.harness_digest,
            "isolation_profile": self.manifest.isolation_profile,
            "workspace_digest": "f" * 64,
            "image_digest": self.image_digest,
            "sandbox_id": "sandbox-1",
            "stage": "candidate",
            "environment_digest": self.manifest.environment_digest,
            "policy_digest": self.manifest.policy_digest,
            "controls": list(CONTROLS),
        }
        unsigned = ArenaIsolationAttestation(
            campaign_digest=self.manifest.digest,
            run_receipt_digest=canonical_digest(self.run_receipt),
            image_digest=self.image_digest,
            sandbox_id="sandbox-1",
            signer_id="isolation-operator-v1",
            signature="pending",
            issued_at_epoch=1500.0,
            expires_at_epoch=2500.0,
            controls=CONTROLS,
            observed_host_identity="trusted-host-01",
            observation_started_at_epoch=1600.0,
            observation_ended_at_epoch=1700.0,
            signer_key_id="isolation-key-v3",
        )
        self.signature = hashlib.sha256(unsigned.signing_message()).hexdigest()
        self.attestation = {
            "campaign_digest": self.manifest.digest,
            "run_receipt_digest": canonical_digest(self.run_receipt),
            "image_digest": self.image_digest,
            "sandbox_id": "sandbox-1",
            "signer_id": "isolation-operator-v1",
            "signature": self.signature,
            "issued_at_epoch": 1500.0,
            "expires_at_epoch": 2500.0,
            "controls": list(CONTROLS),
            "observed_host_identity": "trusted-host-01",
            "observation_started_at_epoch": 1600.0,
            "observation_ended_at_epoch": 1700.0,
            "signer_key_id": "isolation-key-v3",
        }
        self.receipt = {
            "schema_version": "auren-isolation-receipt-v1",
            "manifest_digest": self.manifest.digest,
            "task_digest": self.manifest.task_digest,
            "harness_digest": self.harness_digest,
            "isolation_profile": self.manifest.isolation_profile,
            "isolation_enforced": True,
            "egress_restricted": True,
            "secrets_absent": True,
            "workspace_digest": self.run_receipt["workspace_digest"],
            "stage": "candidate",
            "run_receipt": self.run_receipt,
            "attestation": self.attestation,
        }

        def resolve_context(receipt, manifest, digest):
            return TrustedHarnessRunContext(
                expected_image_digest=self.image_digest,
                expected_sandbox_identity="trusted-host-01",
                campaign_started_at_epoch=1550.0,
                campaign_ended_at_epoch=1800.0,
            )

        self.resolve_context = resolve_context
        self.verify_signature = lambda signer, signature, message: (
            signer == "isolation-operator-v1"
            and signature == hashlib.sha256(message).hexdigest()
        )
        self.verifier = self.make_verifier()

    def make_verifier(self, *, max_future_skew_seconds=30.0):
        return ArenaHarnessIsolationReceiptVerifier(
            verify_detached_signature=self.verify_signature,
            resolve_trusted_context=self.resolve_context,
            trusted_signer_ids=frozenset({"isolation-operator-v1"}),
            revoked_signer_ids=frozenset(),
            trusted_signer_key_ids=frozenset({"isolation-key-v3"}),
            revoked_signer_key_ids=frozenset(),
            evaluator_id="independent-evaluator-v1",
            oracle_id="independent-oracle-v1",
            policy_runtime_id="policy-runtime-v1",
            clock=lambda: self.now,
            max_future_skew_seconds=max_future_skew_seconds,
        )

    def test_valid_signed_receipt_is_bound_to_exact_manifest_task_and_harness(self):
        self.assertTrue(self.verifier(self.receipt, self.manifest, self.harness_digest))

    def test_unsigned_claims_without_attestation_fail_closed(self):
        receipt = dict(self.receipt)
        receipt.pop("attestation")
        self.assertFalse(self.verifier(receipt, self.manifest, self.harness_digest))

    def test_tampered_run_receipt_invalidates_signed_digest(self):
        receipt = dict(self.receipt)
        run_receipt = dict(self.run_receipt)
        run_receipt["workspace_digest"] = "0" * 64
        receipt["run_receipt"] = run_receipt
        receipt["workspace_digest"] = "0" * 64
        self.assertFalse(self.verifier(receipt, self.manifest, self.harness_digest))

    def test_receipt_cannot_select_its_own_trusted_image_or_host(self):
        receipt = dict(self.receipt)
        run_receipt = dict(self.run_receipt)
        run_receipt["image_digest"] = "sha256:" + "9" * 64
        receipt["run_receipt"] = run_receipt
        self.assertFalse(self.verifier(receipt, self.manifest, self.harness_digest))

    def test_wrong_manifest_or_harness_digest_fails_closed(self):
        self.assertFalse(self.verifier(self.receipt, self.manifest, "0" * 64))
        other_manifest = HarnessExperimentManifest(
            schema_version="auren-harness-experiment-v1",
            run_id="different-run",
            task_digest=self.manifest.task_digest,
            base_harness_digest=self.manifest.base_harness_digest,
            executor_id=self.manifest.executor_id,
            evaluator_id=self.manifest.evaluator_id,
            seed=self.manifest.seed,
            max_candidates=self.manifest.max_candidates,
            max_depth=self.manifest.max_depth,
            isolation_profile=self.manifest.isolation_profile,
            policy_digest=self.manifest.policy_digest,
            environment_digest=self.manifest.environment_digest,
        )
        self.assertFalse(self.verifier(self.receipt, other_manifest, self.harness_digest))

    def test_expired_attestation_fails_closed(self):
        self.now = 2600.0
        self.assertFalse(self.verifier(self.receipt, self.manifest, self.harness_digest))

    def test_non_finite_or_boolean_clock_skew_configuration_is_rejected(self):
        for invalid in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "finite and non-negative"):
                    self.make_verifier(max_future_skew_seconds=invalid)


if __name__ == "__main__":
    unittest.main()
