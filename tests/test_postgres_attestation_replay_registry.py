from __future__ import annotations

import unittest

from portable.postgres_attestation_replay_registry import (
    PostgreSQLAttestationReplayRegistry,
    ReplayClaimOutcomeUnknown,
)


class FakeCursor:
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
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.result

    def close(self):
        pass


class FakeConnection:
    def __init__(self, store, *, fail_commit=False):
        self.store = store
        self.fail_commit = fail_commit

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.fail_commit:
            raise OSError("connection lost after commit request")

    def rollback(self):
        pass

    def close(self):
        pass


class PostgreSQLAttestationReplayRegistryTests(unittest.TestCase):
    def test_shared_registry_clients_have_exactly_one_winner(self):
        store = set()
        registry_a = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        registry_b = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        self.assertTrue(registry_a.claim("attestation-1"))
        self.assertFalse(registry_b.claim("attestation-1"))

    def test_unknown_commit_outcome_fails_closed(self):
        store = set()
        connections = [FakeConnection(store), FakeConnection(store, fail_commit=True)]
        def connection_factory():
            if not connections:
                raise AssertionError("unexpected extra connection")
            return connections.pop(0)
        registry = PostgreSQLAttestationReplayRegistry(connection_factory)
        with self.assertRaises(ReplayClaimOutcomeUnknown) as caught:
            registry.claim("attestation-uncertain")
        self.assertEqual("attestation-uncertain", caught.exception.attestation_id)
        self.assertTrue(registry.lookup_claim("attestation-uncertain"))

    def test_registry_is_not_production_ready_without_verified_deployment_evidence(self):
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(set()))
        self.assertIs(registry.production_ready, False)
        self.assertTrue(registry.durable)
        self.assertTrue(registry.multi_host)

    def test_deployment_evidence_requires_literal_true_from_trusted_verifier(self):
        evidence = {
            "deployment_id": "staging-shared-postgres",
            "backend": "postgresql",
            "shared_primary": True,
            "tls_enabled": True,
            "independent_host_count": 2,
            "failover_policy_id": "failover-policy-v1",
            "retention_policy_id": "retention-policy-v1",
            "schema_version": "1",
        }
        with self.assertRaisesRegex(PermissionError, "failed closed"):
            PostgreSQLAttestationReplayRegistry(
                lambda: FakeConnection(set()),
                deployment_attestation=evidence,
                verify_deployment_attestation=lambda _evidence: "true",
            )
        verified = PostgreSQLAttestationReplayRegistry(
            lambda: FakeConnection(set()),
            deployment_attestation=evidence,
            verify_deployment_attestation=lambda value: value == evidence,
        )
        self.assertIs(verified.production_ready, True)

    def test_lookup_claim_is_read_only_and_validates_identifier(self):
        store = set()
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        self.assertFalse(registry.lookup_claim("attestation-lookup"))
        self.assertTrue(registry.claim("attestation-lookup"))
        self.assertTrue(registry.lookup_claim("attestation-lookup"))
        with self.assertRaises(ValueError):
            registry.lookup_claim("  ")

    def test_invalid_table_name_is_rejected(self):
        with self.assertRaises(ValueError):
            PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(set()), table_name="bad; DROP TABLE x")

    def test_empty_attestation_id_is_rejected(self):
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(set()))
        with self.assertRaises(ValueError):
            registry.claim("  ")


if __name__ == "__main__":
    unittest.main()
