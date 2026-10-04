"""Opaque sealed Arena boundary for independently evaluated campaigns.

AUREN receives case digests and externally attested outcomes, never benchmark
answers or an in-process oracle.  This module validates the trust envelope and
returns evidence; it has no promotion or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Final, Mapping

_FORBIDDEN_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset({"answer", "expected_answer", "oracle_answer", "gold", "solution"})


@dataclass(frozen=True, slots=True)
class SealedCaseEnvelope:
    case_id: str
    task_digest: str
    input_digest: str
    environment_digest: str
    holdout: bool
    transfer_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("case_id", self.case_id), ("task_digest", self.task_digest), ("input_digest", self.input_digest), ("environment_digest", self.environment_digest)):
            if not str(value).strip():
                raise ValueError(f"{name} is required")

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "task_digest": self.task_digest,
            "input_digest": self.input_digest,
            "environment_digest": self.environment_digest,
            "holdout": self.holdout,
            "transfer_dimensions": self.transfer_dimensions,
        }


@dataclass(frozen=True, slots=True)
class SealedCampaignRequest:
    campaign_digest: str
    corpus_digest: str
    cases: tuple[SealedCaseEnvelope, ...]

    def __post_init__(self) -> None:
        if not self.campaign_digest.strip() or not self.corpus_digest.strip():
            raise ValueError("campaign_digest and corpus_digest are required")
        if not self.cases:
            raise ValueError("sealed campaign requires cases")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("sealed campaign contains duplicate case ids")
        if not any(case.holdout for case in self.cases):
            raise ValueError("sealed campaign requires at least one holdout")

    def validate_task_metadata(self, payload: Mapping[str, object]) -> None:
        forbidden = sorted(str(key).lower() for key in payload if str(key).lower() in _FORBIDDEN_PAYLOAD_KEYS)
        if forbidden:
            raise ValueError(f"sealed task metadata contains benchmark answer material: {forbidden}")


@dataclass(frozen=True, slots=True)
class ExternalOutcomeReceipt:
    campaign_digest: str
    corpus_digest: str
    evaluator_id: str
    oracle_id: str
    signature: str
    results: tuple[tuple[str, bool, str], ...]
    contamination_free: bool = True
    independent_oracle: bool = True

    def __post_init__(self) -> None:
        for name, value in (("campaign_digest", self.campaign_digest), ("corpus_digest", self.corpus_digest), ("evaluator_id", self.evaluator_id), ("oracle_id", self.oracle_id), ("signature", self.signature)):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.evaluator_id == self.oracle_id:
            raise ValueError("evaluator and oracle must be distinct principals")
        if not self.results:
            raise ValueError("outcome receipt requires results")
        if len({row[0] for row in self.results}) != len(self.results):
            raise ValueError("outcome receipt contains duplicate case ids")
        if any(not case_id.strip() or not evidence_digest.strip() for case_id, _passed, evidence_digest in self.results):
            raise ValueError("outcome receipt contains incomplete result rows")

    @property
    def receipt_digest(self) -> str:
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "evaluator_id": self.evaluator_id,
            "oracle_id": self.oracle_id,
            "results": self.results,
            "contamination_free": self.contamination_free,
            "independent_oracle": self.independent_oracle,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SealedArenaEvidence:
    receipt_digest: str
    holdout_pass_rate: float
    verified_case_count: int


class SealedArenaBoundary:
    """Accept externally attested outcomes without importing the oracle."""

    def __init__(self, verify_signature) -> None:
        if not callable(verify_signature):
            raise TypeError("verify_signature must be callable")
        self._verify_signature = verify_signature

    def accept(self, request: SealedCampaignRequest, receipt: ExternalOutcomeReceipt) -> SealedArenaEvidence:
        if receipt.campaign_digest != request.campaign_digest or receipt.corpus_digest != request.corpus_digest:
            raise ValueError("outcome receipt lineage does not match sealed campaign")
        if not receipt.contamination_free or not receipt.independent_oracle:
            raise ValueError("sealed Arena rejects contaminated or non-independent outcomes")
        if not self._verify_signature(receipt):
            raise ValueError("external outcome signature is not verified")
        expected_ids = {case.case_id for case in request.cases}
        observed_ids = {row[0] for row in receipt.results}
        if not observed_ids <= expected_ids:
            raise ValueError("outcome receipt contains an unknown case")
        holdouts = {case.case_id for case in request.cases if case.holdout}
        holdout_results = [row for row in receipt.results if row[0] in holdouts]
        if not holdout_results:
            raise ValueError("sealed outcome contains no holdout result")
        passed = sum(row[1] for row in holdout_results)
        return SealedArenaEvidence(receipt.receipt_digest, passed / len(holdout_results), len(receipt.results))


__all__ = ["SealedCaseEnvelope", "SealedCampaignRequest", "ExternalOutcomeReceipt", "SealedArenaEvidence", "SealedArenaBoundary"]
