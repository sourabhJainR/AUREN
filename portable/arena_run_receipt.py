"""Content-addressed receipts for independent generalization arena runs.

This records measurements without allowing the adaptive runtime to alter the
corpus, oracle, or gate policy. Promotion chains must bind the receipt to the
exact external campaign digest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from dataclasses import replace
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
    rubric_digest: str = ""
    rubric_receipt_digest: str = ""
    rubric_status: str = ""
    plan_digest: str = ""
    evaluator_id: str = ""
    executor_id: str = ""

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
        for name in ("rubric_digest", "rubric_receipt_digest", "plan_digest"):
            value = getattr(self, name)
            if value and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.rubric_status and self.rubric_status not in {"passed", "failed", "blocked", "unscored"}:
            raise ValueError("invalid rubric_status")
        if self.rubric_receipt_digest and not all((
            self.rubric_digest, self.rubric_status, self.plan_digest, self.evaluator_id.strip(),
        )):
            raise ValueError("a bound rubric receipt requires digest, status, plan and evaluator identity")
        if self.evaluator_id and not self.evaluator_id.strip():
            raise ValueError("evaluator_id cannot be whitespace")
        if self.executor_id and not self.executor_id.strip():
            raise ValueError("executor_id cannot be whitespace")
        if self.evaluator_id and self.executor_id and self.evaluator_id == self.executor_id:
            raise ValueError("Arena evaluator cannot be the executor")
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
        if self.rubric_receipt_digest:
            payload.update({
                "rubric_digest": self.rubric_digest,
                "rubric_receipt_digest": self.rubric_receipt_digest,
                "rubric_status": self.rubric_status,
                "plan_digest": self.plan_digest,
                "evaluator_id": self.evaluator_id,
                "executor_id": self.executor_id,
            })
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
        rubric_ok = not self.rubric_receipt_digest or self.rubric_status == "passed"
        return not self.contamination_detected and bool(self.oracle_digest) and bool(self.corpus_digest) and rubric_ok

    def as_dict(self) -> dict[str, object]:
        return {**self._payload(), "receipt_digest": self.receipt_digest}


def attach_rubric_receipt(arena_receipt: ArenaRunReceipt, rubric_receipt: object) -> ArenaRunReceipt:
    """Bind the canonical rubric receipt to an Arena run without changing its run identity."""
    if not isinstance(arena_receipt, ArenaRunReceipt):
        raise TypeError("arena_receipt must be ArenaRunReceipt")
    for name in ("run_id", "rubric_digest", "receipt_digest", "status", "plan_digest", "evaluator_id", "executor_id"):
        if not isinstance(getattr(rubric_receipt, name, None), str) or not getattr(rubric_receipt, name).strip():
            raise ValueError(f"rubric receipt is missing {name}")
    if rubric_receipt.run_id != arena_receipt.run_id:
        raise ValueError("rubric receipt run_id does not match Arena run")
    if not re.fullmatch(r"[0-9a-f]{64}", rubric_receipt.rubric_digest):
        raise ValueError("rubric digest must be a lowercase SHA-256 value")
    if not re.fullmatch(r"[0-9a-f]{64}", rubric_receipt.receipt_digest):
        raise ValueError("rubric receipt digest must be a lowercase SHA-256 value")
    if not re.fullmatch(r"[0-9a-f]{64}", rubric_receipt.plan_digest):
        raise ValueError("plan digest must be a lowercase SHA-256 value")
    if not isinstance(rubric_receipt.evaluator_id, str) or not rubric_receipt.evaluator_id.strip():
        raise ValueError("evaluator identity is required")
    if rubric_receipt.evaluator_id == rubric_receipt.executor_id:
        raise ValueError("executor cannot act as Arena evaluator")
    return replace(
        arena_receipt,
        rubric_digest=rubric_receipt.rubric_digest,
        rubric_receipt_digest=rubric_receipt.receipt_digest,
        rubric_status=rubric_receipt.status,
        plan_digest=rubric_receipt.plan_digest,
        evaluator_id=rubric_receipt.evaluator_id,
        executor_id=rubric_receipt.executor_id,
        receipt_digest="",
    )


def oracle_registry_digest(oracle_ids: Iterable[str]) -> str:
    """Create a deterministic identity for the independently supplied oracle set."""
    ids = tuple(sorted({str(value).strip() for value in oracle_ids if str(value).strip()}))
    if not ids:
        raise ValueError("at least one oracle id is required")
    return hashlib.sha256(
        json.dumps(ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = ["ArenaRunReceipt", "attach_rubric_receipt", "oracle_registry_digest"]
