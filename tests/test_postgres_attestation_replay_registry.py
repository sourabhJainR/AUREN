from __future__ import annotations

import multiprocessing
import os
import time
import unittest
from concurrent.futures import ProcessPoolExecutor
from uuid import uuid4

from portable.postgres_attestation_replay_registry import (
    PostgreSQLAttestationReplayRegistry,
    ReplayClaimConflict,
    ReplayClaimOutcomeUnknown,
)


def _binding(**overrides):
    value = {
        "attestation_digest": "a" * 64,
        "issuer_key_id": "arena-key-v1",
        "audience": "auren-arena",
        "purpose": "external-evaluation",
        "expires_at_epoch": int(time.time()) + 3600,
        "policy_version": "freshness-v1",
        "policy_digest": "b" * 64,
    }
    value.update(overrides)
    return value


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE UNIQUE INDEX")):
            return
        if "INSERT INTO" in normalized and "ON CONFLICT (attestation_id)" in normalized:
            (
                attestation_id, binding_digest, attestation_digest, issuer_key_id,
                audience, purpose, expires_at_epoch, policy_version, policy_digest,
                operation_id,
            ) = params
            if attestation_id in self.connection.store:
                self.result = None
                return
            if any(row["claim_operation_id"] == operation_id for row in self.connection.store.values()):
                raise RuntimeError("claim operation ID is already bound to another attestation")
            self.connection.store[attestation_id] = {
                "binding_digest": binding_digest,
                "attestation_digest": attestation_digest,
                "issuer_key_id": issuer_key_id,
                "audience": audience,
                "purpose": purpose,
                "expires_at_epoch": expires_at_epoch,
                "policy_version": policy_version,
                "policy_digest": policy_digest,
                "claim_operation_id": operation_id,
            }
            self.result = (attestation_id,)
            return
        if normalized.startswith("SELECT binding_digest, claim_operation_id"):
            row = self.connection.store.get(params[0])
            self.result = None if row is None else (row["binding_digest"], row["claim_operation_id"])
            return
        if normalized.startswith("SELECT attestation_id FROM") and "claim_operation_id" in normalized:
            found = next(
                (key for key, row in self.connection.store.items()
                 if row["claim_operation_id"] == params[0]),
                None,
            )
            self.result = None if found is None else (found,)
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

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


def _claim_postgres(dsn, table_name, attestation_id, operation_id, binding):
    import psycopg

    registry = PostgreSQLAttestationReplayRegistry(
        lambda: psycopg.connect(dsn),
        table_name=table_name,
    )
    return registry.claim(attestation_id, binding=binding, operation_id=operation_id)


def _lookup_postgres(dsn, table_name, attestation_id, operation_id, binding):
    import psycopg

    registry = PostgreSQLAttestationReplayRegistry(
        lambda: psycopg.connect(dsn),
        table_name=table_name,
    )
    return registry.lookup_claim(attestation_id, binding=binding, operation_id=operation_id)


class PostgreSQLAttestationReplayRegistryTests(unittest.TestCase):
    def test_shared_registry_clients_have_one_winner_and_idempotent_same_operation(self):
        store = {}
        registry_a = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        registry_b = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        binding = _binding()
        self.assertTrue(registry_a.claim("attestation-1", binding=binding, operation_id="claim-a"))
        self.assertTrue(registry_b.claim("attestation-1", binding=binding, operation_id="claim-a"))
        self.assertFalse(registry_b.claim("attestation-1", binding=binding, operation_id="claim-b"))

    def test_same_attestation_id_with_different_binding_is_a_conflict(self):
        store = {}
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        registry.claim("attestation-2", binding=_binding(), operation_id="claim-2")
        with self.assertRaises(ReplayClaimConflict):
            registry.claim(
                "attestation-2",
                binding=_binding(purpose="different-purpose"),
                operation_id="claim-2b",
            )

    def test_operation_id_cannot_be_reused_for_another_attestation(self):
        store = {}
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        registry.claim("attestation-3", binding=_binding(), operation_id="claim-3")
        with self.assertRaisesRegex(RuntimeError, "operation ID is already bound"):
            registry.claim("attestation-4", binding=_binding(), operation_id="claim-3")

    def test_unknown_commit_outcome_fails_closed_and_reconciles_same_operation(self):
        store = {}
        connections = [
            FakeConnection(store),
            FakeConnection(store, fail_commit=True),
            FakeConnection(store),
        ]

        def connection_factory():
            if not connections:
                raise AssertionError("unexpected extra connection")
            return connections.pop(0)

        registry = PostgreSQLAttestationReplayRegistry(connection_factory)
        binding = _binding()
        with self.assertRaises(ReplayClaimOutcomeUnknown) as caught:
            registry.claim("attestation-uncertain", binding=binding, operation_id="claim-uncertain")
        self.assertEqual("attestation-uncertain", caught.exception.attestation_id)
        self.assertEqual("claim-uncertain", caught.exception.operation_id)
        self.assertTrue(registry.lookup_claim(
            "attestation-uncertain", binding=binding, operation_id="claim-uncertain",
        ))

    def test_lookup_is_false_for_a_different_operation_after_replay(self):
        store = {}
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(store))
        binding = _binding()
        registry.claim("attestation-lookup", binding=binding, operation_id="claim-winner")
        self.assertFalse(registry.lookup_claim(
            "attestation-lookup", binding=binding, operation_id="claim-loser",
        ))

    def test_registry_is_not_production_ready_without_verified_deployment_evidence(self):
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection({}))
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
            "schema_version": "2",
            "binding_schema_version": "1",
        }
        with self.assertRaisesRegex(PermissionError, "failed closed"):
            PostgreSQLAttestationReplayRegistry(
                lambda: FakeConnection({}),
                deployment_attestation=evidence,
                verify_deployment_attestation=lambda _evidence: "true",
            )
        verified = PostgreSQLAttestationReplayRegistry(
            lambda: FakeConnection({}),
            deployment_attestation=evidence,
            verify_deployment_attestation=lambda value: value == evidence,
        )
        self.assertIs(verified.production_ready, True)

    def test_production_freshness_accepts_only_verified_postgres_registry(self):
        from portable.attestation_freshness import AttestationFreshnessPolicy, FreshnessAwareAttestor
        from portable.evaluator_trust import ProductionSeparationAwareAttestor

        evidence = {
            "deployment_id": "staging-shared-postgres",
            "backend": "postgresql",
            "shared_primary": True,
            "tls_enabled": True,
            "independent_host_count": 2,
            "failover_policy_id": "failover-policy-v1",
            "retention_policy_id": "retention-policy-v1",
            "schema_version": "2",
            "binding_schema_version": "1",
        }
        registry = PostgreSQLAttestationReplayRegistry(
            lambda: FakeConnection({}),
            deployment_attestation=evidence,
            verify_deployment_attestation=lambda value: value == evidence,
        )
        production_attestor = object.__new__(ProductionSeparationAwareAttestor)
        verifier = FreshnessAwareAttestor(
            None, None, AttestationFreshnessPolicy(),
            replay_registry=registry,
            production_attestor=production_attestor,
        )
        self.assertIs(verifier.replay_registry, registry)

    def test_invalid_table_name_is_rejected(self):
        with self.assertRaises(ValueError):
            PostgreSQLAttestationReplayRegistry(lambda: FakeConnection({}), table_name="bad; DROP TABLE x")

    def test_expired_attestation_binding_is_rejected_before_backend_access(self):
        calls = 0
        store = {}

        def connection_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeConnection(store)
            raise AssertionError("expired claim must be rejected before opening the backend")

        registry = PostgreSQLAttestationReplayRegistry(connection_factory)
        with self.assertRaisesRegex(ValueError, "has expired"):
            registry.claim(
                "attestation-expired",
                binding=_binding(expires_at_epoch=int(time.time()) - 1),
                operation_id="claim-expired",
            )

    def test_invalid_binding_and_empty_operation_id_are_rejected(self):
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection({}))
        with self.assertRaises(ValueError):
            registry.claim("attestation-5", binding={"attestation_digest": "a" * 64}, operation_id="op")
        with self.assertRaises(ValueError):
            registry.claim("attestation-5", binding=_binding(), operation_id="  ")

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_real_postgres_claim_is_atomic_across_independent_processes_and_survives_restart(self):
        import psycopg

        dsn = os.environ["AUREN_TEST_POSTGRES_DSN"]
        table_name = "a_replay_" + uuid4().hex[:24]
        binding = _binding()
        operation_ids = [f"claim-{index}" for index in range(24)]
        registry = PostgreSQLAttestationReplayRegistry(lambda: psycopg.connect(dsn), table_name=table_name)
        try:
            with ProcessPoolExecutor(
                max_workers=8,
                mp_context=multiprocessing.get_context("spawn"),
            ) as pool:
                results = list(pool.map(
                    _claim_postgres,
                    [dsn] * len(operation_ids),
                    [table_name] * len(operation_ids),
                    ["same-attestation"] * len(operation_ids),
                    operation_ids,
                    [binding] * len(operation_ids),
                ))
            self.assertEqual(1, sum(results))
            winning_operation = operation_ids[results.index(True)]
            restarted = PostgreSQLAttestationReplayRegistry(
                lambda: psycopg.connect(dsn), table_name=table_name,
            )
            self.assertTrue(restarted.lookup_claim(
                "same-attestation", binding=binding, operation_id=winning_operation,
            ))
            with self.assertRaises(ReplayClaimConflict):
                restarted.claim(
                    "different-attestation",
                    binding=binding,
                    operation_id=winning_operation,
                )
            self.assertTrue(restarted.claim(
                "same-attestation", binding=binding, operation_id=winning_operation,
            ))
            loser = next(value for value in operation_ids if value != winning_operation)
            self.assertFalse(restarted.claim(
                "same-attestation", binding=binding, operation_id=loser,
            ))
        finally:
            with psycopg.connect(dsn) as connection:
                connection.execute(f"DROP TABLE IF EXISTS {table_name}")


    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_real_postgres_lost_commit_ack_reconciles_from_a_fresh_process(self):
        import psycopg

        dsn = os.environ["AUREN_TEST_POSTGRES_DSN"]
        table_name = "a_replay_" + uuid4().hex[:24]
        binding = _binding()
        attestation_id = "lost-ack-" + uuid4().hex
        operation_id = "claim-" + uuid4().hex

        class LostCommitAcknowledgement:
            def __init__(self, connection):
                self.connection = connection

            def cursor(self):
                return self.connection.cursor()

            def commit(self):
                self.connection.commit()
                # Simulate the network dropping the commit response after the
                # database has durably accepted the transaction.
                raise OSError("simulated lost PostgreSQL commit acknowledgement")

            def rollback(self):
                return self.connection.rollback()

            def close(self):
                return self.connection.close()

        calls = 0

        def connection_factory():
            nonlocal calls
            calls += 1
            connection = psycopg.connect(dsn)
            # Registry construction performs schema setup first. Wrap only the
            # subsequent claim connection so setup itself remains successful.
            return LostCommitAcknowledgement(connection) if calls == 2 else connection

        registry = PostgreSQLAttestationReplayRegistry(
            connection_factory, table_name=table_name,
        )
        try:
            with self.assertRaises(ReplayClaimOutcomeUnknown) as caught:
                registry.claim(attestation_id, binding=binding, operation_id=operation_id)
            self.assertEqual(attestation_id, caught.exception.attestation_id)
            self.assertEqual(operation_id, caught.exception.operation_id)

            # A separate spawned process and fresh database connection must
            # reconcile the exact operation, not create a second claim.
            with ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            ) as pool:
                reconciled = pool.submit(
                    _lookup_postgres, dsn, table_name, attestation_id,
                    operation_id, binding,
                ).result(timeout=20)
            self.assertTrue(reconciled)

            restarted = PostgreSQLAttestationReplayRegistry(
                lambda: psycopg.connect(dsn), table_name=table_name,
            )
            self.assertTrue(restarted.claim(
                attestation_id, binding=binding, operation_id=operation_id,
            ))
            self.assertFalse(restarted.claim(
                attestation_id, binding=binding, operation_id=operation_id + "-replay",
            ))
        finally:
            with psycopg.connect(dsn) as connection:
                connection.execute(f"DROP TABLE IF EXISTS {table_name}")



if __name__ == "__main__":
    unittest.main()
