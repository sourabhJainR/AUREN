"""Shared PostgreSQL-backed attestation replay claims.

This adapter requires a transactional shared PostgreSQL database. It never
falls back to process-local or SQLite storage. An uncertain commit is surfaced
as an unknown outcome and must be reconciled by the caller, not blindly retried.
"""
from __future__ import annotations

from typing import Callable, Any


class ReplayClaimOutcomeUnknown(RuntimeError):
    """The shared store may have committed a claim, but its outcome is unknown."""

    def __init__(self, attestation_id: str) -> None:
        self.attestation_id = attestation_id
        super().__init__(
            "attestation replay claim outcome is unknown; fail closed and reconcile the same ID"
        )


class PostgreSQLAttestationReplayRegistry:
    """Atomic replay claims backed by a shared PostgreSQL primary.

    `connection_factory` must return a fresh DB-API connection connected to
    the same strongly consistent PostgreSQL primary for all verifier hosts.
    The caller owns credentials, TLS, failover policy, and connection pooling.
    """

    durable = True
    multi_host = True

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        table_name: str = "auren_consumed_attestations",
        deployment_attestation: dict[str, Any] | None = None,
        verify_deployment_attestation: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if not isinstance(table_name, str) or not table_name.isidentifier():
            raise ValueError("table_name must be a simple SQL identifier")
        self.production_ready = False
        if deployment_attestation is not None or verify_deployment_attestation is not None:
            if not isinstance(deployment_attestation, dict) or not callable(verify_deployment_attestation):
                raise ValueError("deployment attestation and its trusted verifier must be supplied together")
            required_text = (
                "deployment_id", "failover_policy_id", "retention_policy_id", "schema_version",
            )
            if any(
                not isinstance(deployment_attestation.get(field), str)
                or not deployment_attestation[field].strip()
                for field in required_text
            ):
                raise ValueError("deployment attestation is missing required identity or policy fields")
            if (
                deployment_attestation.get("backend") != "postgresql"
                or deployment_attestation.get("shared_primary") is not True
                or deployment_attestation.get("tls_enabled") is not True
                or type(deployment_attestation.get("independent_host_count")) is not int
                or deployment_attestation["independent_host_count"] < 2
            ):
                raise ValueError("deployment attestation does not establish the required shared PostgreSQL topology")
            try:
                verified = verify_deployment_attestation(dict(deployment_attestation)) is True
            except Exception:
                verified = False
            if not verified:
                raise PermissionError("PostgreSQL deployment attestation failed closed")
            self.production_ready = True
        self._connection_factory = connection_factory
        self._table = table_name
        connection = connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._table} ("
                    "attestation_id TEXT PRIMARY KEY, "
                    "claimed_at_epoch BIGINT NOT NULL)"
                )
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    def lookup_claim(self, attestation_id: str) -> bool:
        """Read whether an attestation ID is consumed on the configured primary.

        This is a reconciliation aid after an ambiguous commit, not proof that
        the caller's request won the original claim. A true result must never be
        converted into acceptance after ReplayClaimOutcomeUnknown.
        """
        if not isinstance(attestation_id, str) or not attestation_id.strip():
            raise ValueError("attestation_id is required")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"SELECT attestation_id FROM {self._table} WHERE attestation_id = %s",
                    (attestation_id,),
                )
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

    def claim(self, attestation_id: str) -> bool:
        if not isinstance(attestation_id, str) or not attestation_id.strip():
            raise ValueError("attestation_id is required")
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"INSERT INTO {self._table}(attestation_id, claimed_at_epoch) "
                    "VALUES (%s, EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)::BIGINT) "
                    "ON CONFLICT (attestation_id) DO NOTHING RETURNING attestation_id",
                    (attestation_id,),
                )
                claimed = cursor.fetchone() is not None
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
            finally:
                cursor.close()
            try:
                connection.commit()
            except Exception as exc:
                # The transaction may have committed before the connection was
                # lost. Do not report rejection or acceptance; callers must
                # reconcile against the same id using an explicit read path.
                raise ReplayClaimOutcomeUnknown(attestation_id) from exc
            return claimed
        finally:
            connection.close()


__all__ = ["PostgreSQLAttestationReplayRegistry", "ReplayClaimOutcomeUnknown"]
