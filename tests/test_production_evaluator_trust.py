from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from dataclasses import replace
import time

from portable.independent_evaluation_attestation import EvaluationAttestation
from portable.attestation_freshness import (
    AttestationFreshnessPolicy,
    SQLiteAttestationReplayRegistry,
    FreshEvaluationAttestation,
    FreshnessAwareAttestor,
)
from portable.postgres_attestation_replay_registry import PostgreSQLAttestationReplayRegistry
from portable.evaluator_trust import (
    Ed25519EvaluationAttestationVerifier,
    EvaluatorKeyWindow,
    PinnedPrincipalVerifier,
    ProductionSeparationAwareAttestor,
    encode_evaluation_signature,
    evaluation_attestation_signing_message,
)

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    serialization = None
    Ed25519PrivateKey = None


def public_bytes(private_key):
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class _ReplayCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def execute(self, sql, params=None):
        if sql.startswith("CREATE TABLE"):
            return
        if "INSERT INTO" in sql and "ON CONFLICT" in sql:
            key = params[0]
            if key in self.connection.store:
                self.result = None
            else:
                self.connection.store.add(key)
                self.result = (key,)
            return
        if sql.startswith("SELECT attestation_id FROM"):
            self.result = (params[0],) if params[0] in self.connection.store else None
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.result

    def close(self):
        pass


class _ReplayConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _ReplayCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def production_replay_registry(store):
    evidence = {
        "deployment_id": "test-shared-postgres",
        "backend": "postgresql",
        "shared_primary": True,
        "tls_enabled": True,
        "independent_host_count": 2,
        "failover_policy_id": "test-failover-v1",
        "retention_policy_id": "test-retention-v1",
        "schema_version": "1",
    }
    return PostgreSQLAttestationReplayRegistry(
        lambda: _ReplayConnection(store),
        deployment_attestation=evidence,
        verify_deployment_attestation=lambda value: value == evidence,
    )


@unittest.skipIf(Ed25519PrivateKey is None, "optional cryptography dependency is not installed")
class ProductionEvaluatorTrustTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.clock = lambda: self.now
        self.private = Ed25519PrivateKey.generate()
        self.key = EvaluatorKeyWindow(
            key_id="evaluator-key-2026q4",
            signer_id="independent-attestor",
            public_key=public_bytes(self.private),
            not_before_epoch=self.now - 120,
            not_after_epoch=self.now + 3600,
        )

    def attestation(self, *, key_id=None, signer_id="independent-attestor", private_key=None, issued=None, expires=None):
        issued = self.now - 1 if issued is None else issued
        expires = self.now + 60 if expires is None else expires
        value = EvaluationAttestation(
            campaign_digest="campaign-digest",
            corpus_digest="sealed-corpus-digest",
            oracle_digest="oracle-digest",
            evaluator_version="evaluator-build-17",
            signer_id=signer_id,
            signature="placeholder",
            key_id=key_id or self.key.key_id,
            run_id="sealed-run-41",
            receipt_digest="receipt-digest",
            runtime_digest="candidate-runtime-digest",
            issued_at_epoch=issued,
            expires_at_epoch=expires,
        )
        private_key = private_key or self.private
        signature = private_key.sign(evaluation_attestation_signing_message(value))
        return replace(value, signature=encode_evaluation_signature(signature))

    def verifier(self, keys=None, *, revoked_signers=()):
        keyring = Ed25519EvaluationAttestationVerifier(
            keys or (self.key,),
            clock=self.clock,
            revoked_signers=revoked_signers,
        )
        principal_verifier = PinnedPrincipalVerifier(
            lambda principal, role: (principal, role) in {
                ("evaluator-service", "evaluator"),
                ("oracle-service", "oracle"),
            },
            provider_id="enterprise-identity-provider",
            provider_version="oidc-claims-v3",
            trust_source_ref="oidc://issuer.example/tenant",
            configuration_digest="a" * 64,
        )
        attestor = ProductionSeparationAwareAttestor(keyring, principal_verifier)
        return keyring, attestor

    def verify(self, attestor, value):
        return attestor.verify(
            value,
            evaluator_id="evaluator-service",
            oracle_id="oracle-service",
            expected_campaign_digest="campaign-digest",
            expected_corpus_digest="sealed-corpus-digest",
            expected_oracle_digest="oracle-digest",
        )

    def test_valid_ed25519_signature_and_lineage_pass_production_trust_boundary(self):
        _, attestor = self.verifier()
        result = self.verify(attestor, self.attestation())
        self.assertTrue(result.verified)
        self.assertTrue(result.independent)
        self.assertTrue(result.trustworthy)
        self.assertTrue(result.production_trustworthy)
        self.assertEqual(result.attestation.key_id, "evaluator-key-2026q4")

    def test_production_seal_survives_freshness_and_replay_verification(self):
        keyring, production_attestor = self.verifier()
        with tempfile.TemporaryDirectory() as directory:
            freshness = FreshnessAwareAttestor(
                None,
                None,
                AttestationFreshnessPolicy(max_age_seconds=900, max_future_skew_seconds=30),
                replay_registry=production_replay_registry(set()),
                production_attestor=production_attestor,
            )
            signed = self.attestation()
            result = freshness.verify(
                FreshEvaluationAttestation(signed, issued_at=int(self.now - 1)),
                evaluator_id="evaluator-service",
                oracle_id="oracle-service",
                expected_campaign_digest="campaign-digest",
                expected_corpus_digest="sealed-corpus-digest",
                expected_oracle_digest="oracle-digest",
                now=int(self.now),
            )
            self.assertTrue(result.trustworthy)
            self.assertTrue(result.production_trustworthy)

    def test_production_freshness_rejects_replay_after_registry_restart(self):
        _, production_attestor = self.verifier()
        with tempfile.TemporaryDirectory() as directory:
            replay_store = set()
            signed = self.attestation()
            fresh = FreshEvaluationAttestation(signed, issued_at=int(self.now - 1))
            first = FreshnessAwareAttestor(
                None, None,
                AttestationFreshnessPolicy(max_age_seconds=900, max_future_skew_seconds=30),
                replay_registry=production_replay_registry(replay_store),
                production_attestor=production_attestor,
            )
            accepted = first.verify(
                fresh, evaluator_id="evaluator-service", oracle_id="oracle-service",
                expected_campaign_digest="campaign-digest",
                expected_corpus_digest="sealed-corpus-digest",
                expected_oracle_digest="oracle-digest", now=int(self.now),
            )
            self.assertTrue(accepted.production_trustworthy)

            restarted = FreshnessAwareAttestor(
                None, None,
                AttestationFreshnessPolicy(max_age_seconds=900, max_future_skew_seconds=30),
                replay_registry=SQLiteAttestationReplayRegistry(replay_db),
                production_attestor=production_attestor,
            )
            replay = restarted.verify(
                fresh, evaluator_id="evaluator-service", oracle_id="oracle-service",
                expected_campaign_digest="campaign-digest",
                expected_corpus_digest="sealed-corpus-digest",
                expected_oracle_digest="oracle-digest", now=int(self.now),
            )
            self.assertFalse(replay.trustworthy)
            self.assertFalse(replay.production_trustworthy)
            self.assertTrue(any("already been consumed" in reason for reason in replay.reasons))

    def test_bad_signature_expired_attestation_and_wrong_signer_fail_closed(self):
        _, attestor = self.verifier()
        valid = self.attestation()
        bad_signature = replace(valid, signature=encode_evaluation_signature(bytes(64)))
        self.assertFalse(self.verify(attestor, bad_signature).trustworthy)
        expired = self.attestation(issued=self.now - 120, expires=self.now - 1)
        self.assertFalse(self.verify(attestor, expired).trustworthy)
        wrong_signer = self.attestation(signer_id="untrusted-attestor")
        self.assertFalse(self.verify(attestor, wrong_signer).trustworthy)

    def test_key_rotation_allows_new_key_and_revokes_old_key_without_revoking_signer(self):
        old_private = Ed25519PrivateKey.generate()
        old_key = EvaluatorKeyWindow(
            key_id="evaluator-key-2026q3",
            signer_id="independent-attestor",
            public_key=public_bytes(old_private),
            not_before_epoch=self.now - 1000,
            not_after_epoch=self.now + 100,
            revoked=True,
        )
        _, attestor = self.verifier((old_key, self.key))
        new_result = self.verify(attestor, self.attestation())
        self.assertTrue(new_result.trustworthy)
        old_attestation = self.attestation(
            key_id=old_key.key_id, private_key=old_private,
        )
        self.assertFalse(self.verify(attestor, old_attestation).trustworthy)

    def test_whole_signer_revocation_blocks_every_key_for_that_identity(self):
        _, attestor = self.verifier(revoked_signers=("independent-attestor",))
        self.assertFalse(self.verify(attestor, self.attestation()).trustworthy)

    def test_production_attestor_rejects_protocol_only_verifiers_and_unpinned_identity(self):
        with self.assertRaisesRegex(TypeError, "Ed25519EvaluationAttestationVerifier"):
            ProductionSeparationAwareAttestor(
                lambda _attestation: True,
                PinnedPrincipalVerifier(
                    lambda _principal, _role: True,
                    provider_id="enterprise-identity-provider",
                    provider_version="oidc-claims-v3",
                    trust_source_ref="oidc://issuer.example/tenant",
                    configuration_digest="a" * 64,
                ),
            )
        keyring = Ed25519EvaluationAttestationVerifier((self.key,), clock=self.clock)
        with self.assertRaisesRegex(TypeError, "PinnedPrincipalVerifier"):
            ProductionSeparationAwareAttestor(keyring, lambda _principal, _role: True)
        with self.assertRaisesRegex(ValueError, "test or protocol-only"):
            PinnedPrincipalVerifier(
                lambda _principal, _role: True,
                provider_id="test",
                provider_version="oidc-claims-v3",
                trust_source_ref="oidc://issuer.example/tenant",
                configuration_digest="a" * 64,
            )


if __name__ == "__main__":
    unittest.main()
