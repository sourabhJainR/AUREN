"""Real PostgreSQL concurrency and fencing tests for deployment admission."""
from __future__ import annotations

import multiprocessing
import os
import unittest
from concurrent.futures import ProcessPoolExecutor
from uuid import uuid4


def _claim_slot(dsn, namespace, policy, resource, owner, operation, capacity):
    import psycopg
    from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator

    coordinator = PostgreSQLAdmissionCoordinator(
        lambda: psycopg.connect(dsn), namespace=namespace, policy_version=policy,
    )
    lease = coordinator.acquire(resource, owner, operation, capacity=capacity, lease_seconds=30)
    return lease is not None


def _claim_work_key(dsn, namespace, policy, work_key, digest, owner, task_id):
    import psycopg
    from portable.postgres_admission_coordinator import (
        DuplicateWorkKeySubmission, PostgreSQLAdmissionCoordinator,
    )

    coordinator = PostgreSQLAdmissionCoordinator(
        lambda: psycopg.connect(dsn), namespace=namespace, policy_version=policy,
    )
    try:
        identity = coordinator.claim_work_key(work_key, digest, owner, task_id)
        return ("claimed", identity.owner, identity.task_id)
    except DuplicateWorkKeySubmission:
        return ("duplicate", owner, None)


class PostgreSQLAdmissionCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ.get("AUREN_TEST_POSTGRES_DSN")
        self.namespace = "test-" + uuid4().hex
        self.policy = "test-v1"

    def _coordinator(self):
        import psycopg
        from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator

        return PostgreSQLAdmissionCoordinator(
            lambda: psycopg.connect(self.dsn),
            namespace=self.namespace,
            policy_version=self.policy,
            provision=True,
        )

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_atomic_capacity_across_independent_processes(self):
        coordinator = self._coordinator()
        resource = "workers-" + uuid4().hex
        owners = [f"host-{index}" for index in range(20)]
        operations = [f"operation-{index}" for index in range(20)]
        with ProcessPoolExecutor(
            max_workers=10,
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            results = list(pool.map(
                _claim_slot,
                [self.dsn] * len(owners),
                [self.namespace] * len(owners),
                [self.policy] * len(owners),
                [resource] * len(owners),
                owners,
                operations,
                [3] * len(owners),
            ))
        self.assertEqual(sum(results), 3)
        self.assertEqual(coordinator.active_count(resource), 3)

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_idempotency_renewal_expiry_and_fencing(self):
        import psycopg

        coordinator = self._coordinator()
        resource = "worker-" + uuid4().hex
        lease = coordinator.acquire(resource, "host-a:task-a", "task-a", capacity=1, lease_seconds=30)
        self.assertIsNotNone(lease)
        duplicate = coordinator.acquire(resource, "host-a:task-a", "task-a", capacity=1, lease_seconds=30)
        self.assertEqual(duplicate, lease)
        self.assertTrue(coordinator.renew(lease, lease_seconds=30))
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "UPDATE auren_admission_leases SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE namespace=%s AND resource=%s AND operation_id=%s",
                (self.namespace, resource, "task-a"),
            )
        replacement = coordinator.acquire(resource, "host-a:task-a", "task-a", capacity=1, lease_seconds=30)
        self.assertIsNotNone(replacement)
        self.assertGreater(replacement.generation, lease.generation)
        self.assertFalse(coordinator.renew(lease, lease_seconds=30))
        self.assertFalse(coordinator.release(lease))
        self.assertTrue(coordinator.release(replacement))

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_durable_work_key_identity_recovers_and_rejects_cross_root_duplicates(self):
        from portable.postgres_admission_coordinator import (
            DuplicateWorkKeySubmission, WorkKeyIntentConflict,
        )

        coordinator = self._coordinator()
        key = "work-" + uuid4().hex
        digest = __import__("hashlib").sha256(b"immutable-intent").hexdigest()
        first = coordinator.claim_work_key(key, digest, "runtime-a", "task-canonical")
        replay = coordinator.claim_work_key(key, digest, "runtime-a", "task-retry")
        self.assertEqual(first.task_id, replay.task_id)
        self.assertEqual("task-canonical", replay.task_id)
        with self.assertRaises(DuplicateWorkKeySubmission):
            coordinator.claim_work_key(key, digest, "runtime-b", "task-duplicate")
        other_digest = __import__("hashlib").sha256(b"different-intent").hexdigest()
        with self.assertRaises(WorkKeyIntentConflict):
            coordinator.claim_work_key(key, other_digest, "runtime-a", "task-mutated")

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_competing_processes_cannot_claim_same_work_key(self):
        coordinator = self._coordinator()
        key = "contended-" + uuid4().hex
        digest = __import__("hashlib").sha256(b"shared-intent").hexdigest()
        owners = ["runtime-a", "runtime-b"]
        task_ids = ["task-a", "task-b"]
        with ProcessPoolExecutor(
            max_workers=2, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            results = list(pool.map(
                _claim_work_key,
                [self.dsn, self.dsn],
                [self.namespace, self.namespace],
                [self.policy, self.policy],
                [key, key],
                [digest, digest],
                owners,
                task_ids,
            ))
        claimed = [item for item in results if item[0] == "claimed"]
        duplicates = [item for item in results if item[0] == "duplicate"]
        self.assertEqual(1, len(claimed))
        self.assertEqual(1, len(duplicates))
        owner, task_id = claimed[0][1], claimed[0][2]
        replay = coordinator.claim_work_key(key, digest, owner, "retry-task-id")
        self.assertEqual(task_id, replay.task_id)

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_work_key_identity_is_permanent_and_namespace_scoped(self):
        coordinator = self._coordinator()
        key = "permanent-" + uuid4().hex
        digest = __import__("hashlib").sha256(b"intent").hexdigest()
        identity = coordinator.claim_work_key(key, digest, "runtime-a", "task-a")
        self.assertEqual("task-a", identity.task_id)
        # Capacity leases may expire; a canonical task identity must not.
        with __import__("psycopg").connect(self.dsn) as connection:
            connection.execute(
                "UPDATE auren_admission_leases SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 day' "
                "WHERE namespace=%s AND operation_id=%s",
                (self.namespace, "task-a"),
            )
        replay = coordinator.claim_work_key(key, digest, "runtime-a", "task-b")
        self.assertEqual("task-a", replay.task_id)
        from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator
        other_namespace = PostgreSQLAdmissionCoordinator(
            lambda: __import__("psycopg").connect(self.dsn),
            namespace=self.namespace + "-other",
            policy_version=self.policy,
            provision=True,
        )
        separate = other_namespace.claim_work_key(
            key, digest, "runtime-b", "task-other-namespace"
        )
        self.assertEqual("task-other-namespace", separate.task_id)

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_capacity_policy_mismatch_fails_closed(self):
        coordinator = self._coordinator()
        resource = "policy-" + uuid4().hex
        self.assertIsNotNone(coordinator.acquire(resource, "host-a:one", "one", capacity=2))
        from portable.postgres_admission_coordinator import AdmissionPolicyConflict
        with self.assertRaises(AdmissionPolicyConflict):
            coordinator.acquire(resource, "host-b:two", "two", capacity=3)

    @unittest.skipUnless(os.environ.get("AUREN_TEST_POSTGRES_DSN"), "real PostgreSQL service is not configured")
    def test_policy_version_mismatch_fails_closed(self):
        self._coordinator()
        import psycopg
        from portable.postgres_admission_coordinator import PostgreSQLAdmissionCoordinator, AdmissionPolicyConflict

        other = PostgreSQLAdmissionCoordinator(
            lambda: psycopg.connect(self.dsn),
            namespace=self.namespace,
            policy_version="different-policy",
        )
        with self.assertRaises(AdmissionPolicyConflict):
            other.acquire("resource", "host", "operation", capacity=1)


if __name__ == "__main__":
    unittest.main()
