"""PostgreSQL-backed deployment-wide admission leases.

This coordinator is deliberately opt-in. It requires one shared PostgreSQL
service reachable by every participating host; local SQLite registries are
not a multi-host substitute. All capacity decisions use PostgreSQL time and
transaction-scoped advisory locks.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable


class AdmissionCoordinatorUnavailable(RuntimeError):
    """The shared admission authority cannot safely answer a request."""


class AdmissionPolicyConflict(RuntimeError):
    """Hosts disagree on the deployment namespace or capacity policy."""


class DuplicateWorkKeySubmission(RuntimeError):
    """A work key is already owned by another runtime in this deployment."""


class WorkKeyIntentConflict(RuntimeError):
    """A deployment work key was reused for a different immutable intent."""


@dataclass(frozen=True)
class WorkKeyIdentity:
    namespace: str
    work_key: str
    intent_digest: str
    owner: str
    task_id: str
    created_at: Any


@dataclass(frozen=True)
class AdmissionLease:
    namespace: str
    resource: str
    owner: str
    operation_id: str
    token: str
    generation: int
    expires_at: Any


class PostgreSQLAdmissionCoordinator:
    """Atomic, owner-fenced admission across processes and separate hosts.

    connection_factory must return a PostgreSQL DB-API connection (for example,
    psycopg.connect). The service role needs CREATE/ALTER rights during
    provisioning and normal DML rights at runtime. Provision schema ahead of
    production and use provision=False for restricted runtime roles.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        namespace: str,
        policy_version: str,
        provision: bool = False,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        for name, value in (("namespace", namespace), ("policy_version", policy_version)):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must be a non-empty string of at most 200 characters")
        self._connect = connection_factory
        self.namespace = namespace.strip()
        self.policy_version = policy_version.strip()
        if provision:
            self.provision()

    def provision(self) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auren_admission_policy (
                        namespace TEXT PRIMARY KEY,
                        policy_version TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auren_admission_leases (
                        namespace TEXT NOT NULL,
                        resource TEXT NOT NULL,
                        owner TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        token TEXT NOT NULL,
                        generation BIGINT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(namespace, resource, owner),
                        UNIQUE(namespace, resource, operation_id)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auren_admission_generations (
                        namespace TEXT NOT NULL,
                        resource TEXT NOT NULL,
                        generation BIGINT NOT NULL,
                        PRIMARY KEY(namespace, resource)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auren_work_key_identities (
                        namespace TEXT NOT NULL,
                        work_key TEXT NOT NULL,
                        intent_digest TEXT NOT NULL,
                        owner TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(namespace, work_key),
                        UNIQUE(namespace, task_id)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auren_admission_resource_policy (
                        namespace TEXT NOT NULL,
                        resource TEXT NOT NULL,
                        capacity INTEGER NOT NULL CHECK(capacity > 0),
                        PRIMARY KEY(namespace, resource)
                    )
                """)
                cursor.execute(
                    "INSERT INTO auren_admission_policy(namespace,policy_version) VALUES(%s,%s) "
                    "ON CONFLICT(namespace) DO NOTHING",
                    (self.namespace, self.policy_version),
                )
                cursor.execute(
                    "SELECT policy_version FROM auren_admission_policy WHERE namespace=%s",
                    (self.namespace,),
                )
                row = cursor.fetchone()
                if row is None or row[0] != self.policy_version:
                    raise AdmissionPolicyConflict("deployment admission policy version mismatch")
            finally:
                cursor.close()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _text(name: str, value: str, maximum: int = 256) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
        return value.strip()

    def _connect_runtime(self, operation: str) -> Any:
        """Normalize connection-establishment failures into a fail-closed result."""
        try:
            return self._connect()
        except Exception as exc:
            raise AdmissionCoordinatorUnavailable(
                f"shared admission {operation} cannot connect; operation must fail closed"
            ) from exc

    def _lock(self, cursor: Any, resource: str) -> None:
        # Hashing is stable across hosts; collisions only serialize unrelated
        # admission operations and cannot weaken the capacity invariant.
        key = hashlib.sha256((self.namespace + "\0" + resource).encode()).hexdigest()[:15]
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (int(key, 16),))

    def acquire(
        self,
        resource: str,
        owner: str,
        operation_id: str,
        *,
        capacity: int,
        lease_seconds: int = 30,
    ) -> AdmissionLease | None:
        resource = self._text("resource", resource, 128)
        owner = self._text("owner", owner)
        operation_id = self._text("operation_id", operation_id)
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError("capacity must be an integer between 1 and 100000")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be an integer between 1 and 86400")
        connection = self._connect_runtime("lease acquisition")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT policy_version FROM auren_admission_policy WHERE namespace=%s",
                    (self.namespace,),
                )
                policy = cursor.fetchone()
                if policy is None or policy[0] != self.policy_version:
                    raise AdmissionPolicyConflict("shared admission policy is missing or mismatched")
                self._lock(cursor, resource)
                cursor.execute(
                    "INSERT INTO auren_admission_resource_policy(namespace,resource,capacity) VALUES(%s,%s,%s) "
                    "ON CONFLICT(namespace,resource) DO NOTHING",
                    (self.namespace, resource, capacity),
                )
                cursor.execute(
                    "SELECT capacity FROM auren_admission_resource_policy WHERE namespace=%s AND resource=%s",
                    (self.namespace, resource),
                )
                stored_capacity = cursor.fetchone()
                if stored_capacity is None or int(stored_capacity[0]) != capacity:
                    raise AdmissionPolicyConflict("hosts supplied different capacity for the same deployment resource")
                cursor.execute(
                    "SELECT owner,token,generation,expires_at FROM auren_admission_leases "
                    "WHERE namespace=%s AND resource=%s AND operation_id=%s",
                    (self.namespace, resource, operation_id),
                )
                prior = cursor.fetchone()
                if prior is not None:
                    if prior[0] != owner:
                        raise AdmissionPolicyConflict("operation_id is already bound to another owner")
                    cursor.execute("SELECT CURRENT_TIMESTAMP")
                    now = cursor.fetchone()[0]
                    if prior[3] > now:
                        lease = AdmissionLease(self.namespace, resource, owner, operation_id, prior[1], int(prior[2]), prior[3])
                        connection.commit()
                        return lease
                    # The same stable operation may be retried after its old
                    # lease expires. Delete only under the resource lock, then
                    # issue a fresh fencing generation below.
                    cursor.execute(
                        "DELETE FROM auren_admission_leases WHERE namespace=%s AND resource=%s "
                        "AND owner=%s AND operation_id=%s AND token=%s",
                        (self.namespace, resource, owner, operation_id, prior[1]),
                    )
                cursor.execute(
                    "DELETE FROM auren_admission_leases WHERE namespace=%s AND resource=%s AND expires_at<=CURRENT_TIMESTAMP",
                    (self.namespace, resource),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM auren_admission_leases WHERE namespace=%s AND resource=%s",
                    (self.namespace, resource),
                )
                if int(cursor.fetchone()[0]) >= capacity:
                    connection.commit()
                    return None
                cursor.execute(
                    "INSERT INTO auren_admission_generations(namespace,resource,generation) VALUES(%s,%s,1) "
                    "ON CONFLICT(namespace,resource) DO UPDATE SET generation=auren_admission_generations.generation+1 "
                    "RETURNING generation",
                    (self.namespace, resource),
                )
                generation = int(cursor.fetchone()[0])
                token = uuid.uuid4().hex
                cursor.execute(
                    "INSERT INTO auren_admission_leases(namespace,resource,owner,operation_id,token,generation,expires_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')) "
                    "RETURNING expires_at",
                    (self.namespace, resource, owner, operation_id, token, generation, lease_seconds),
                )
                expires_at = cursor.fetchone()[0]
                connection.commit()
                return AdmissionLease(self.namespace, resource, owner, operation_id, token, generation, expires_at)
            finally:
                cursor.close()
        except (AdmissionPolicyConflict, ValueError):
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            raise AdmissionCoordinatorUnavailable("shared admission claim failed; host must fail closed") from exc
        finally:
            connection.close()

    def claim_work_key(
        self, work_key: str, intent_digest: str, owner: str, task_id: str
    ) -> WorkKeyIdentity:
        """Atomically bind one deployment work key to one immutable task identity.

        The identity is permanent: it is not a capacity lease and must not expire.
        A retry by the same stable runtime owner with the same digest recovers the
        original task ID, including after a crash before local SQLite insertion.
        Other owners are rejected rather than creating a second local task.
        """
        work_key = self._text("work_key", work_key, 256)
        intent_digest = self._text("intent_digest", intent_digest, 128)
        owner = self._text("owner", owner)
        task_id = self._text("task_id", task_id, 128)
        if len(intent_digest) != 64 or any(ch not in "0123456789abcdef" for ch in intent_digest):
            raise ValueError("intent_digest must be a lowercase SHA-256 hex digest")
        connection = self._connect_runtime("work-key identity claim")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT policy_version FROM auren_admission_policy WHERE namespace=%s",
                    (self.namespace,),
                )
                policy = cursor.fetchone()
                if policy is None or policy[0] != self.policy_version:
                    raise AdmissionPolicyConflict("shared admission policy is missing or mismatched")
                lock_key = "work-key:" + hashlib.sha256(work_key.encode("utf-8")).hexdigest()
                self._lock(cursor, lock_key)
                cursor.execute(
                    "INSERT INTO auren_work_key_identities(namespace,work_key,intent_digest,owner,task_id) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(namespace,work_key) DO NOTHING",
                    (self.namespace, work_key, intent_digest, owner, task_id),
                )
                cursor.execute(
                    "SELECT intent_digest,owner,task_id,created_at FROM auren_work_key_identities "
                    "WHERE namespace=%s AND work_key=%s",
                    (self.namespace, work_key),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AdmissionCoordinatorUnavailable("work-key identity could not be read after claim")
                if row[0] != intent_digest:
                    raise WorkKeyIntentConflict(
                        "work_key is already bound to a different immutable task intent"
                    )
                if row[1] != owner:
                    raise DuplicateWorkKeySubmission(
                        f"work_key is already owned by runtime {row[1]!r}; canonical task_id={row[2]}"
                    )
                connection.commit()
                return WorkKeyIdentity(self.namespace, work_key, row[0], row[1], row[2], row[3])
            finally:
                cursor.close()
        except (AdmissionPolicyConflict, DuplicateWorkKeySubmission, WorkKeyIntentConflict, ValueError):
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            raise AdmissionCoordinatorUnavailable(
                "shared work-key identity claim failed; submission must fail closed"
            ) from exc
        finally:
            connection.close()

    def renew(self, lease: AdmissionLease, *, lease_seconds: int = 30) -> bool:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be an integer between 1 and 86400")
        connection = self._connect_runtime("lease renewal")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE auren_admission_leases SET expires_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'), "
                    "updated_at=CURRENT_TIMESTAMP WHERE namespace=%s AND resource=%s AND owner=%s "
                    "AND operation_id=%s AND token=%s AND generation=%s AND expires_at>CURRENT_TIMESTAMP",
                    (lease_seconds, lease.namespace, lease.resource, lease.owner, lease.operation_id, lease.token, lease.generation),
                )
                renewed = cursor.rowcount == 1
            finally:
                cursor.close()
            connection.commit()
            return renewed
        except Exception as exc:
            connection.rollback()
            raise AdmissionCoordinatorUnavailable("shared admission renewal failed; owner must stop") from exc
        finally:
            connection.close()

    def release(self, lease: AdmissionLease) -> bool:
        connection = self._connect_runtime("lease release")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "DELETE FROM auren_admission_leases WHERE namespace=%s AND resource=%s AND owner=%s "
                    "AND operation_id=%s AND token=%s AND generation=%s",
                    (lease.namespace, lease.resource, lease.owner, lease.operation_id, lease.token, lease.generation),
                )
                released = cursor.rowcount == 1
            finally:
                cursor.close()
            connection.commit()
            return released
        except Exception as exc:
            connection.rollback()
            raise AdmissionCoordinatorUnavailable("shared admission release failed; ownership is unknown") from exc
        finally:
            connection.close()

    def active_count(self, resource: str) -> int:
        resource = self._text("resource", resource, 128)
        connection = self._connect_runtime("admission status read")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM auren_admission_leases WHERE namespace=%s AND resource=%s "
                    "AND expires_at>CURRENT_TIMESTAMP",
                    (self.namespace, resource),
                )
                return int(cursor.fetchone()[0])
            finally:
                cursor.close()
        except Exception as exc:
            raise AdmissionCoordinatorUnavailable("shared admission status is unavailable") from exc
        finally:
            connection.close()


__all__ = [
    "AdmissionCoordinatorUnavailable", "AdmissionPolicyConflict",
    "AdmissionLease", "WorkKeyIdentity", "DuplicateWorkKeySubmission",
    "WorkKeyIntentConflict", "PostgreSQLAdmissionCoordinator",
]
