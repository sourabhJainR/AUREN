"""Shared PostgreSQL-backed attestation replay claims.

The adapter never falls back to process-local or SQLite storage. Production
readiness remains false unless a host-trusted verifier accepts explicit
deployment evidence. Claim bindings and operation IDs make retries idempotent;
an unknown commit is never treated as acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping


_BINDING_FIELDS = frozenset({
    "attestation_digest",
    "issuer_key_id",
    "audience",
    "purpose",
    "expires_at_epoch",
    "policy_version",
    "policy_digest",
})


class ReplayClaimConflict(ValueError):
    """An attestation ID or claim operation ID was reused with different intent."""


class ReplayClaimOutcomeUnknown(RuntimeError):
    """The commit acknowledgement was lost; the caller must reconcile this operation."""

    def __init__(self, attestation_id: str, operation_id: str, binding_digest: str) -> None:
        self.attestation_id = attestation_id
        self.operation_id = operation_id
        self.binding_digest = binding_digest
        super().__init__(
            "attestation replay claim outcome is unknown; fail closed and reconcile "
            "with the same attestation ID, binding, and operation ID"
        )


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _canonical_binding(binding: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(binding, Mapping) or set(binding) != _BINDING_FIELDS:
        raise ValueError("binding must contain exactly the required attestation trust-context fields")
    normalized = {
        "attestation_digest": _required_text("attestation_digest", binding.get("attestation_digest")),
        "issuer_key_id": _required_text("issuer_key_id", binding.get("issuer_key_id")),
        "audience": _required_text("audience", binding.get("audience")),
        "purpose": _required_text("purpose", binding.get("purpose")),
        "expires_at_epoch": binding.get("expires_at_epoch"),
        "policy_version": _required_text("policy_version", binding.get("policy_version")),
        "policy_digest": _required_text("policy_digest", binding.get("policy_digest")),
    }
    for field in ("attestation_digest", "policy_digest"):
        value = normalized[field]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError(f"{field} must be a SHA-256 hex digest")
        normalized[field] = value.lower()
    expiry = normalized["expires_at_epoch"]
    if type(expiry) is not int or expiry <= 0:
        raise ValueError("expires_at_epoch must be a positive integer")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return normalized, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PostgreSQLAttestationReplayRegistry:
    """Atomic replay claims backed by one shared, strongly consistent PostgreSQL primary.

    The caller owns credentials, TLS, pooling, and failover. A claim is bound to
    the attestation digest, issuer key, audience, purpose, expiry, and policy
    version/digest. Reusing the same operation ID with the same binding is
    idempotent; another operation ID is rejected as replay. A binding mismatch
    is a conflict and fails closed.
    """

    durable = True
    multi_host = True

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        table_name: str = "auren_consumed_attestations",
        deployment_attestation: Mapping[str, Any] | None = None,
        verify_deployment_attestation: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if (
            not isinstance(table_name, str)
            or len(table_name) > 40
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None
        ):
            raise ValueError("table_name must be a simple SQL identifier of at most 40 characters")
        production_ready = False
        if deployment_attestation is not None or verify_deployment_attestation is not None:
            if not isinstance(deployment_attestation, Mapping) or not callable(verify_deployment_attestation):
                raise ValueError("deployment attestation and its trusted verifier must be supplied together")
            evidence = dict(deployment_attestation)
            for field in (
                "deployment_id", "failover_policy_id", "retention_policy_id",
                "schema_version", "binding_schema_version",
            ):
                _required_text(field, evidence.get(field))
            if (
                evidence.get("backend") != "postgresql"
                or evidence.get("shared_primary") is not True
                or evidence.get("tls_enabled") is not True
                or type(evidence.get("independent_host_count")) is not int
                or evidence["independent_host_count"] < 2
                or evidence.get("schema_version") != "2"
                or evidence.get("binding_schema_version") != "1"
            ):
                raise ValueError("deployment attestation does not establish the required shared PostgreSQL topology")
            try:
                production_ready = verify_deployment_attestation(evidence) is True
            except Exception:
                production_ready = False
            if not production_ready:
                raise PermissionError("PostgreSQL deployment attestation failed closed")

        self.production_ready = False
        self._connection_factory = connection_factory
        self._table = table_name
        connection = connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._table} ("
                    "attestation_id TEXT PRIMARY KEY, "
                    "binding_digest TEXT NOT NULL, "
                    "attestation_digest TEXT NOT NULL, "
                    "issuer_key_id TEXT NOT NULL, "
                    "audience TEXT NOT NULL, "
                    "purpose TEXT NOT NULL, "
                    "expires_at_epoch BIGINT NOT NULL, "
                    "policy_version TEXT NOT NULL, "
                    "policy_digest TEXT NOT NULL, "
                    "claim_operation_id TEXT NOT NULL, "
                    "claimed_at_epoch BIGINT NOT NULL)"
                )
                # Additive migration for tables created by the earlier
                # single-field prototype. Legacy rows remain NULL in these
                # columns and are rejected on replay rather than trusted.
                for column, sql_type in (
                    ("binding_digest", "TEXT"),
                    ("attestation_digest", "TEXT"),
                    ("issuer_key_id", "TEXT"),
                    ("audience", "TEXT"),
                    ("purpose", "TEXT"),
                    ("expires_at_epoch", "BIGINT"),
                    ("policy_version", "TEXT"),
                    ("policy_digest", "TEXT"),
                    ("claim_operation_id", "TEXT"),
                ):
                    cursor.execute(
                        f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"
                    )
                cursor.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {self._table}_operation_id_unique "
                    f"ON {self._table}(claim_operation_id) WHERE claim_operation_id IS NOT NULL"
                )
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()
        self.production_ready = production_ready

    def claim(
        self,
        attestation_id: str,
        *,
        binding: Mapping[str, Any],
        operation_id: str,
    ) -> bool:
        identifier = _required_text("attestation_id", attestation_id)
        operation = _required_text("operation_id", operation_id)
        normalized, binding_digest = _canonical_binding(binding)
        if normalized["expires_at_epoch"] <= int(time.time()):
            raise ValueError("attestation binding has expired")
        connection = self._connection_factory()
        cursor = None
        try:
            try:
                cursor = connection.cursor()
                cursor.execute(
                    f"INSERT INTO {self._table}("
                    "attestation_id,binding_digest,attestation_digest,issuer_key_id,audience,purpose,"
                    "expires_at_epoch,policy_version,policy_digest,claim_operation_id,claimed_at_epoch"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)::BIGINT) "
                    "ON CONFLICT (attestation_id) DO NOTHING RETURNING attestation_id",
                    (
                        identifier, binding_digest, normalized["attestation_digest"],
                        normalized["issuer_key_id"], normalized["audience"], normalized["purpose"],
                        normalized["expires_at_epoch"], normalized["policy_version"],
                        normalized["policy_digest"], operation,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is not None:
                    claimed = True
                else:
                    cursor.execute(
                        f"SELECT binding_digest, claim_operation_id FROM {self._table} "
                        "WHERE attestation_id = %s",
                        (identifier,),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise ReplayClaimOutcomeUnknown(identifier, operation, binding_digest)
                    stored_digest, stored_operation = existing
                    if not stored_digest or not stored_operation:
                        raise ReplayClaimConflict(
                            "legacy replay claim lacks binding metadata; operator migration is required"
                        )
                    if stored_digest != binding_digest:
                        raise ReplayClaimConflict(
                            "attestation ID is already bound to a different trust context"
                        )
                    claimed = stored_operation == operation
            except ReplayClaimConflict:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
            except Exception as exc:
                try:
                    connection.rollback()
                except Exception:
                    pass
                # The attestation primary-key conflict is handled by
                # ON CONFLICT above. A remaining PostgreSQL unique violation
                # therefore indicates operation-ID reuse across attestations.
                sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
                if sqlstate == "23505":
                    raise ReplayClaimConflict(
                        "claim operation ID is already bound to another attestation"
                    ) from exc
                raise
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass
            try:
                connection.commit()
            except Exception as exc:
                raise ReplayClaimOutcomeUnknown(identifier, operation, binding_digest) from exc
            return claimed
        finally:
            connection.close()

    def lookup_claim(
        self,
        attestation_id: str,
        *,
        binding: Mapping[str, Any],
        operation_id: str,
    ) -> bool:
        """Reconcile an ambiguous commit for the same stable operation ID.

        True means this exact operation ID is durably recorded with this exact
        binding. False means it is absent or another operation consumed the
        attestation. A binding mismatch raises ReplayClaimConflict. Backend
        errors propagate and must leave the caller blocked.
        """
        identifier = _required_text("attestation_id", attestation_id)
        operation = _required_text("operation_id", operation_id)
        _normalized, binding_digest = _canonical_binding(binding)
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"SELECT binding_digest, claim_operation_id FROM {self._table} "
                    "WHERE attestation_id = %s",
                    (identifier,),
                )
                existing = cursor.fetchone()
                if existing is None:
                    return False
                stored_digest, stored_operation = existing
                if not stored_digest or not stored_operation:
                    raise ReplayClaimConflict("legacy replay claim lacks binding metadata; operator migration is required")
                if stored_digest != binding_digest:
                    raise ReplayClaimConflict("attestation ID is already bound to a different trust context")
                return stored_operation == operation
            finally:
                cursor.close()
        finally:
            connection.close()


__all__ = [
    "PostgreSQLAttestationReplayRegistry",
    "ReplayClaimConflict",
    "ReplayClaimOutcomeUnknown",
]
