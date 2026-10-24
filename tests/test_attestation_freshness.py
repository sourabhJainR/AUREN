import unittest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from portable.attestation_freshness import (
    AttestationFreshnessPolicy,
    AttestationReplayRegistry,
    FreshEvaluationAttestation,
    FreshnessAwareAttestor,
    SQLiteAttestationReplayRegistry,
)
from portable.attestation_key_separation import AttestationTrustPolicy
from portable.independent_evaluation_attestation import EvaluationAttestation


def _claim_sqlite_replay(database_path, attestation_id):
    return SQLiteAttestationReplayRegistry(database_path).claim(attestation_id)


def make_attestation(issued_at=100):
    return FreshEvaluationAttestation(
        EvaluationAttestation("campaign", "corpus", "oracle", "v1", "signer", "sig", key_id="signer-key-v1"),
        issued_at,
    )


def verify_principal(principal, role):
    return (role == "evaluator" and principal == "evaluator") or (
        role == "oracle" and principal == "oracle-service"
    )


def make_verifier(registry=None, verify_identity=verify_principal, verify_signature=None):
    if verify_signature is None:
        verify_signature = lambda attestation: attestation.signature == "sig"
    return FreshnessAwareAttestor(
        verify_signature,
        AttestationTrustPolicy(frozenset({"signer"}), trusted_key_ids=frozenset({"signer-key-v1"})),
        AttestationFreshnessPolicy(max_age_seconds=10, max_future_skew_seconds=2),
        registry or AttestationReplayRegistry(),
        verify_principal=verify_identity,
    )


class AttestationFreshnessTests(unittest.TestCase):
    def kwargs(self):
        return dict(
            evaluator_id="evaluator",
            oracle_id="oracle-service",
            expected_campaign_digest="campaign",
            expected_corpus_digest="corpus",
            expected_oracle_digest="oracle",
            now=105,
        )

    def test_production_freshness_requires_durable_replay_registry(self):
        from portable.evaluator_trust import ProductionSeparationAwareAttestor
        production_attestor = object.__new__(ProductionSeparationAwareAttestor)
        with self.assertRaisesRegex(ValueError, "durable SQLiteAttestationReplayRegistry"):
            FreshnessAwareAttestor(
                None, None, AttestationFreshnessPolicy(),
                production_attestor=production_attestor,
            )

    def test_fresh_attestation_is_trustworthy_once(self):
        result = make_verifier().verify(make_attestation(), **self.kwargs())
        self.assertTrue(result.trustworthy)

    def test_replay_is_rejected_and_has_distinct_evidence_digest(self):
        verifier = make_verifier()
        attestation = make_attestation()
        first = verifier.verify(attestation, **self.kwargs())
        replay = verifier.verify(attestation, **self.kwargs())
        self.assertTrue(first.trustworthy)
        self.assertFalse(replay.trustworthy)
        self.assertIn("already been consumed", replay.reasons[-1])
        self.assertNotEqual(first.evidence_digest, replay.evidence_digest)

    def test_expired_attestation_is_rejected(self):
        result = make_verifier().verify(make_attestation(issued_at=100), **{**self.kwargs(), "now": 111})
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("expired" in reason for reason in result.reasons))

    def test_future_skew_is_rejected(self):
        result = make_verifier().verify(make_attestation(issued_at=103), **{**self.kwargs(), "now": 100})
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("future" in reason for reason in result.reasons))

    def test_replay_registry_can_be_preseeded(self):
        attestation = make_attestation()
        registry = AttestationReplayRegistry({attestation.attestation_id})
        result = make_verifier(registry).verify(attestation, **self.kwargs())
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("already been consumed" in reason for reason in result.reasons))

    def test_missing_principal_verifier_fails_closed(self):
        result = make_verifier(verify_identity=None).verify(make_attestation(), **self.kwargs())
        self.assertFalse(result.independent)
        self.assertFalse(result.trustworthy)
        self.assertTrue(any("not configured" in reason for reason in result.reasons))

    def test_invalid_signature_does_not_consume_replay_slot(self):
        registry = AttestationReplayRegistry()
        attestation = make_attestation()
        invalid = make_verifier(registry, verify_signature=lambda _value: False).verify(
            attestation, **self.kwargs()
        )
        valid = make_verifier(registry, verify_signature=lambda _value: True).verify(
            attestation, **self.kwargs()
        )
        self.assertFalse(invalid.trustworthy)
        self.assertTrue(valid.trustworthy)

    def test_concurrent_replay_claim_has_exactly_one_winner(self):
        registry = AttestationReplayRegistry()
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(registry.claim, ["same-attestation"] * 64))
        self.assertEqual(sum(results), 1)

    def test_sqlite_registry_rejects_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestation-replay.sqlite3"
            first_process = SQLiteAttestationReplayRegistry(path)
            self.assertTrue(first_process.claim("attestation-1"))
            restarted_process = SQLiteAttestationReplayRegistry(path)
            self.assertFalse(restarted_process.claim("attestation-1"))
            self.assertTrue(restarted_process.claim("attestation-2"))

    def test_sqlite_registry_allows_exactly_one_cross_instance_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestation-replay.sqlite3"
            registries = [SQLiteAttestationReplayRegistry(path) for _ in range(12)]
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(lambda registry: registry.claim("same-attestation"), registries))
            self.assertEqual(sum(results), 1)

    def test_sqlite_registry_allows_exactly_one_cross_process_claim(self):
        from concurrent.futures import ProcessPoolExecutor

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "attestation-replay.sqlite3")
            # Initialize schema before workers race to claim the same identifier.
            SQLiteAttestationReplayRegistry(path)
            with ProcessPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(_claim_sqlite_replay, [path] * 24, ["same-attestation"] * 24))
            self.assertEqual(sum(results), 1)

    def test_sqlite_registry_rejects_memory_database(self):
        with self.assertRaisesRegex(ValueError, "durable file path"):
            SQLiteAttestationReplayRegistry(":memory:")

    def test_boolean_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            make_attestation(issued_at=True)


if __name__ == "__main__":
    unittest.main()
