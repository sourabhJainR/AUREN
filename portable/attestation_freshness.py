"""Freshness and replay protection for external Arena attestations.

This layer binds issuance time, freshness policy, and one-time-use outcome to
the verified evidence digest. Principal authentication is injected and explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Callable

from .attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from .independent_evaluation_attestation import (
    _PRODUCTION_EVALUATION_SEAL,
    _TRUSTED_EVALUATION_SEAL,
    AttestedEvaluation,
    EvaluationAttestation,
)


@dataclass(frozen=True, slots=True)
class FreshEvaluationAttestation:
    attestation: EvaluationAttestation
    issued_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.attestation, EvaluationAttestation):
            raise TypeError("EvaluationAttestation is required")
        if type(self.issued_at) is not int or self.issued_at < 0:
            raise ValueError("issued_at must be a non-negative integer")

    @property
    def attestation_id(self) -> str:
        payload = {
            "attestation_digest": self.attestation.attestation_digest,
            "issued_at": self.issued_at,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AttestationFreshnessPolicy:
    max_age_seconds: int = 900
    max_future_skew_seconds: int = 30

    def __post_init__(self) -> None:
        if type(self.max_age_seconds) is not int or type(self.max_future_skew_seconds) is not int:
            raise ValueError("freshness windows must be integers")
        if self.max_age_seconds < 0 or self.max_future_skew_seconds < 0:
            raise ValueError("freshness windows must be non-negative")

    def validate(self, attestation: FreshEvaluationAttestation, *, now: int) -> tuple[bool, tuple[str, ...]]:
        if type(now) is not int or now < 0:
            raise ValueError("now must be a non-negative integer")
        if attestation.issued_at > now + self.max_future_skew_seconds:
            return False, ("attestation issuance time is too far in the future",)
        if now - attestation.issued_at > self.max_age_seconds:
            return False, ("attestation has expired",)
        return True, ()


class AttestationReplayRegistry:
    """Thread-safe process-local one-time-use registry.

    Production deployments should inject durable, transactional storage shared
    by all verifier workers; this in-memory implementation only prevents
    concurrent replay inside one process.
    """

    def __init__(self, seen: set[str] | None = None) -> None:
        self._seen = set(seen or ())
        self._lock = threading.Lock()

    def claim(self, attestation_id: str) -> bool:
        if not isinstance(attestation_id, str) or not attestation_id.strip():
            raise ValueError("attestation_id is required")
        with self._lock:
            if attestation_id in self._seen:
                return False
            self._seen.add(attestation_id)
            return True


class SQLiteAttestationReplayRegistry:
    """Durable one-time-use registry shared by verifier processes on one host.

    Each claim takes a SQLite write lock and inserts a primary-keyed attestation
    ID in one transaction. A duplicate ID is rejected, including after process
    restart or when multiple registry instances race. For multi-host deployments,
    use a shared transactional store with equivalent uniqueness semantics.
    """

    def __init__(self, database_path: str | Path, *, timeout_seconds: float = 10.0) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path is required")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("SQLiteAttestationReplayRegistry requires a durable file path")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS consumed_attestations ("
                "attestation_id TEXT PRIMARY KEY, claimed_at_epoch INTEGER NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = %d" % int(self.timeout_seconds * 1000))
        return connection

    def claim(self, attestation_id: str) -> bool:
        if not isinstance(attestation_id, str) or not attestation_id.strip():
            raise ValueError("attestation_id is required")
        import time

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO consumed_attestations(attestation_id, claimed_at_epoch) VALUES (?, ?)",
                    (attestation_id, int(time.time())),
                )
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                return False
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return True


class FreshnessAwareAttestor:
    """Verify lineage, authenticated principal separation, freshness, and replay protection."""

    def __init__(
        self,
        verify_signature: Callable | None,
        policy: AttestationTrustPolicy | None,
        freshness_policy: AttestationFreshnessPolicy,
        replay_registry: AttestationReplayRegistry | None = None,
        verify_principal: Callable[[str, str], bool] | None = None,
        *,
        production_attestor=None,
    ) -> None:
        if not isinstance(freshness_policy, AttestationFreshnessPolicy):
            raise TypeError("AttestationFreshnessPolicy is required")
        if production_attestor is not None:
            from .evaluator_trust import ProductionSeparationAwareAttestor
            if type(production_attestor) is not ProductionSeparationAwareAttestor:
                raise TypeError("production freshness requires ProductionSeparationAwareAttestor")
            if not isinstance(replay_registry, SQLiteAttestationReplayRegistry):
                raise ValueError("production freshness requires a durable SQLiteAttestationReplayRegistry")
            if verify_signature is not None or policy is not None or verify_principal is not None:
                raise ValueError("do not mix production attestor with protocol verifier callbacks")
            self._attestor = production_attestor
        else:
            if not callable(verify_signature) or not isinstance(policy, AttestationTrustPolicy):
                raise TypeError("signature verifier and AttestationTrustPolicy are required")
            self._attestor = SeparationAwareAttestor(verify_signature, policy, verify_principal)
        self.freshness_policy = freshness_policy
        self.replay_registry = replay_registry or AttestationReplayRegistry()

    def verify(
        self,
        attestation: FreshEvaluationAttestation,
        *,
        evaluator_id: str,
        oracle_id: str,
        expected_campaign_digest: str,
        expected_corpus_digest: str,
        expected_oracle_digest: str,
        now: int,
        contamination_detected: bool = False,
    ) -> AttestedEvaluation:
        if not isinstance(attestation, FreshEvaluationAttestation):
            raise TypeError("FreshEvaluationAttestation is required")
        base = self._attestor.verify(
            attestation.attestation,
            evaluator_id=evaluator_id,
            oracle_id=oracle_id,
            expected_campaign_digest=expected_campaign_digest,
            expected_corpus_digest=expected_corpus_digest,
            expected_oracle_digest=expected_oracle_digest,
            contamination_detected=contamination_detected,
        )
        fresh, freshness_reasons = self.freshness_policy.validate(attestation, now=now)
        # Invalid signatures/lineage must not consume the one-time replay slot:
        # otherwise an unauthenticated request could preempt a valid attestation.
        replay_ok = self.replay_registry.claim(attestation.attestation_id) if fresh and base.trustworthy else False
        reasons = list((*base.reasons, *freshness_reasons))
        if fresh and base.trustworthy and not replay_ok:
            reasons.append("attestation has already been consumed")
        trustworthy = base.trustworthy and fresh and replay_ok
        evidence_payload = {
            "base_evidence_digest": base.evidence_digest,
            "attestation_id": attestation.attestation_id,
            "issued_at": attestation.issued_at,
            "verified_at": now,
            "max_age_seconds": self.freshness_policy.max_age_seconds,
            "max_future_skew_seconds": self.freshness_policy.max_future_skew_seconds,
            "fresh": fresh,
            "replay_accepted": replay_ok,
            "trustworthy": trustworthy,
        }
        evidence_digest = hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = AttestedEvaluation(
            base.attestation,
            trustworthy,
            base.independent,
            base.contamination_free,
            tuple(reasons),
            evidence_digest,
        )
        if trustworthy and base.trustworthy:
            object.__setattr__(result, "_trust_seal", _TRUSTED_EVALUATION_SEAL)
        if trustworthy and getattr(base, "production_trustworthy", False):
            object.__setattr__(result, "_production_seal", _PRODUCTION_EVALUATION_SEAL)
        return result


__all__ = [
    "FreshEvaluationAttestation",
    "AttestationFreshnessPolicy",
    "AttestationReplayRegistry",
    "SQLiteAttestationReplayRegistry",
    "FreshnessAwareAttestor",
]
