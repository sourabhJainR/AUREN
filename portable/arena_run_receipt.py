"""Content-addressed receipts for independent generalization arena runs.

This remains evaluation infrastructure only. It records what was evaluated and
the resulting measurements without allowing the adaptive runtime to alter the
corpus, oracle, or gate policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ArenaRunReceipt:
    """Immutable, reproducible summary of one externally controlled arena run."""

    run_id: str
    arena_version: str
    corpus_digest: str
    manifest_digest: str
    oracle_digest: str
    runtime_snapshot: str
    evaluator_version: str
    case_ids: tuple[str, ...]
    holdout_case_ids: tuple[str, ...]
    passed_case_ids: tuple[str, ...]
    verified_case_ids: tuple[str, ...]
    duration_ms: int
    resource_measurements: tuple[tuple[str, str], ...] = ()
    contamination_detected: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("arena_version", self.arena_version),
            ("corpus_digest", self.corpus_digest),
            ("manifest_digest", self.manifest_digest),
            ("oracle_digest", self.oracle_digest),
            ("runtime_snapshot", self.runtime_snapshot),
            ("evaluator_version", self.evaluator_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        if not self.case_ids:
            raise ValueError("at least one case is required")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("duplicate case id")
        if not set(self.holdout_case_ids) <= set(self.case_ids):
            raise ValueError("holdout case is not in evaluated case set")
        if not set(self.passed_case_ids) <= set(self.case_ids):
            raise ValueError("passed case is not in evaluated case set")
        if not set(self.verified_case_ids) <= set(self.case_ids):
            raise ValueError("verified case is not in evaluated case set")
        if any(not k.strip() or not isinstance(v, str) for k, v in self.resource_measurements):
            raise ValueError("resource measurements require non-empty keys and string values")
        if len({k for k, _ in self.resource_measurements}) != len(self.resource_measurements):
            raise ValueError("duplicate resource measurement key")
        expected = self._digest(self._payload())
        if self.receipt_digest and self.receipt_digest != expected:
            raise ValueError("receipt_digest does not match receipt contents")
        object.__setattr__(self, "receipt_digest", expected)

    def _payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "arena_version": self.arena_version,
            "corpus_digest": self.corpus_digest,
            "manifest_digest": self.manifest_digest,
            "oracle_digest": self.oracle_digest,
            "runtime_snapshot": self.runtime_snapshot,
            "evaluator_version": self.evaluator_version,
            "case_ids": self.case_ids,
            "holdout_case_ids": self.holdout_case_ids,
            "passed_case_ids": self.passed_case_ids,
            "verified_case_ids": self.verified_case_ids,
            "duration_ms": self.duration_ms,
            "resource_measurements": self.resource_measurements,
            "contamination_detected": self.contamination_detected,
        }

    @staticmethod
    def _digest(payload: Mapping[str, object]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def holdout_pass_rate(self) -> float:
        holdout = set(self.holdout_case_ids)
        if not holdout:
            return 0.0
        return len(holdout & set(self.passed_case_ids)) / len(holdout)

    @property
    def holdout_verification_rate(self) -> float:
        holdout = set(self.holdout_case_ids)
        if not holdout:
            return 0.0
        return len(holdout & set(self.verified_case_ids)) / len(holdout)

    @property
    def trustworthy(self) -> bool:
        return not self.contamination_detected and bool(self.oracle_digest) and bool(self.corpus_digest)

    def as_dict(self) -> dict[str, object]:
        return {**self._payload(), "receipt_digest": self.receipt_digest}


def oracle_registry_digest(oracle_ids: Iterable[str]) -> str:
    """Create a deterministic identity for the independently supplied oracle set."""
    ids = tuple(sorted({str(value).strip() for value in oracle_ids if str(value).strip()}))
    if not ids:
        raise ValueError("at least one oracle id is required")
    return hashlib.sha256(
        json.dumps(ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = ["ArenaRunReceipt", "oracle_registry_digest"]
