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
        registry = PostgreSQLAttestationReplayRegistry(
            lambda: FakeConnection(store, fail_commit=True)
        )
        with self.assertRaises(ReplayClaimOutcomeUnknown):
            registry.claim("attestation-uncertain")

    def test_invalid_table_name_is_rejected(self):
        with self.assertRaises(ValueError):
            PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(set()), table_name="bad; DROP TABLE x")

    def test_empty_attestation_id_is_rejected(self):
        registry = PostgreSQLAttestationReplayRegistry(lambda: FakeConnection(set()))
        with self.assertRaises(ValueError):
            registry.claim("  ")


if __name__ == "__main__":
    unittest.main()
