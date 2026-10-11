"""Content-addressed receipts for independent generalization arena runs.

This records measurements without allowing the adaptive runtime to alter the
corpus, oracle, or gate policy. Promotion chains must bind the receipt to the
exact external campaign digest.
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
    campaign_digest: str = ""

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
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.campaign_digest, str):
            raise ValueError("campaign_digest must be a string")
        if self.campaign_digest and not self.campaign_digest.strip():
            raise ValueError("campaign_digest cannot be whitespace")
        if type(self.contamination_detected) is not bool:
            raise ValueError("contamination_detected must be a boolean")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        if not isinstance(self.case_ids, tuple) or not self.case_ids:
            raise ValueError("at least one case is required")
        if any(not isinstance(case_id, str) or not case_id.strip() for case_id in self.case_ids):
            raise ValueError("case ids must be non-empty strings")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("duplicate case id")
        if not isinstance(self.holdout_case_ids, tuple) or not isinstance(self.passed_case_ids, tuple) or not isinstance(self.verified_case_ids, tuple):
            raise ValueError("case result collections must be tuples")
        if not set(self.holdout_case_ids) <= set(self.case_ids):
            raise ValueError("holdout case is not in evaluated case set")
        if not set(self.passed_case_ids) <= set(self.case_ids):
            raise ValueError("passed case is not in evaluated case set")
        if not set(self.verified_case_ids) <= set(self.case_ids):
            raise ValueError("verified case is not in evaluated case set")
        if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in self.resource_measurements):
            raise ValueError("resource measurements require non-empty keys and string values")
        if len({k for k, _ in self.resource_measurements}) != len(self.resource_measurements):
            raise ValueError("duplicate resource measurement key")
        expected = self._digest(self._payload())
        if self.receipt_digest and self.receipt_digest != expected:
            raise ValueError("receipt_digest does not match receipt contents")
        object.__setattr__(self, "receipt_digest", expected)

    def _payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
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
        # Preserve the digest format of legacy receipts until they are rebound
        # to a campaign. Promotion-chain acceptance rejects an empty binding.
        if self.campaign_digest:
            payload["campaign_digest"] = self.campaign_digest
        return payload

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
