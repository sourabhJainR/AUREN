"""Shared PostgreSQL-backed attestation replay claims.

This adapter requires a transactional shared PostgreSQL database. It never
falls back to process-local or SQLite storage. An uncertain commit is surfaced
as an unknown outcome and must be reconciled by the caller, not blindly retried.
"""
from __future__ import annotations

from typing import Callable, Any


class ReplayClaimOutcomeUnknown(RuntimeError):
    """The shared store may have committed a claim, but its outcome is unknown."""


class PostgreSQLAttestationReplayRegistry:
    """Atomic replay claims backed by a shared PostgreSQL primary.

    `connection_factory` must return a fresh DB-API connection connected to
    the same strongly consistent PostgreSQL primary for all verifier hosts.
    The caller owns credentials, TLS, failover policy, and connection pooling.
    """

    production_ready = True
    durable = True
    multi_host = True

    def __init__(self, connection_factory: Callable[[], Any], *, table_name: str = "auren_consumed_attestations") -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if not isinstance(table_name, str) or not table_name.isidentifier():
            raise ValueError("table_name must be a simple SQL identifier")
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
                raise ReplayClaimOutcomeUnknown(
                    "attestation replay claim commit outcome is unknown; fail closed and reconcile"
                ) from exc
            return claimed
        finally:
            connection.close()


__all__ = ["PostgreSQLAttestationReplayRegistry", "ReplayClaimOutcomeUnknown"]
