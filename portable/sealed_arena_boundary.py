"""Opaque sealed Arena boundary for independently evaluated campaigns.

AUREN receives case digests and externally attested outcomes, never benchmark
answers or an in-process oracle. This module validates the trust envelope and
returns evidence; it has no promotion or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Final, Mapping

_FORBIDDEN_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset({
    "answer",
    "expected_answer",
    "expected_output",
    "oracle_answer",
    "ground_truth",
    "correct_answer",
    "reference_answer",
    "answer_key",
    "gold",
    "gold_label",
    "solution",
})


def _required_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SealedCaseEnvelope:
    case_id: str
    task_digest: str
    input_digest: str
    environment_digest: str
    holdout: bool
    transfer_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("case_id", self.case_id),
            ("task_digest", self.task_digest),
            ("input_digest", self.input_digest),
            ("environment_digest", self.environment_digest),
        ):
            _required_text(name, value)
        if type(self.holdout) is not bool:
            raise ValueError("holdout must be a boolean")
        if not isinstance(self.transfer_dimensions, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.transfer_dimensions
        ):
            raise ValueError("transfer_dimensions must be a tuple of non-empty strings")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

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
    run_nonce: str = ""

    def __post_init__(self) -> None:
        _required_text("campaign_digest", self.campaign_digest)
        _required_text("corpus_digest", self.corpus_digest)
        _required_text("run_nonce", self.run_nonce)
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("sealed campaign requires a non-empty tuple of cases")
        if any(not isinstance(case, SealedCaseEnvelope) for case in self.cases):
            raise ValueError("sealed campaign cases must be SealedCaseEnvelope values")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("sealed campaign contains duplicate case ids")
        if not any(case.holdout for case in self.cases):
            raise ValueError("sealed campaign requires at least one holdout")

    @property
    def request_digest(self) -> str:
        """Content-address the exact case set, split assignments, nonce and lineage."""
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "run_nonce": self.run_nonce,
            "cases": [case.as_dict() for case in self.cases],
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def validate_task_metadata(self, payload: Mapping[str, object]) -> None:
        forbidden: set[str] = set()

        def inspect(value: object, path: str) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
                    if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                        forbidden.add(f"{path}.{key}")
                    inspect(child, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    inspect(child, f"{path}[{index}]")

        inspect(payload, "payload")
        if forbidden:
            raise ValueError(
                "sealed task metadata contains benchmark answer material: "
                + ", ".join(sorted(forbidden))
            )


@dataclass(frozen=True, slots=True)
class ExternalOutcomeReceipt:
    campaign_digest: str
    corpus_digest: str
    evaluator_id: str
    oracle_id: str
    signature: str
    results: tuple[tuple[str, bool, str], ...]
    contamination_free: bool = False
    independent_oracle: bool = False
    run_nonce: str = ""
    request_digest: str = ""
    # case_id, predicted score, realized score, uncertainty, resource cost ms, recovered
    metrics: tuple[tuple[str, float, float, float, float, bool], ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_digest", self.campaign_digest),
            ("corpus_digest", self.corpus_digest),
            ("evaluator_id", self.evaluator_id),
            ("oracle_id", self.oracle_id),
            ("signature", self.signature),
            ("run_nonce", self.run_nonce),
            ("request_digest", self.request_digest),
        ):
            _required_text(name, value)
        if self.evaluator_id == self.oracle_id:
            raise ValueError("evaluator and oracle must be distinct principals")
        if type(self.contamination_free) is not bool:
            raise ValueError("contamination_free must be a boolean")
        if type(self.independent_oracle) is not bool:
            raise ValueError("independent_oracle must be a boolean")
        if not isinstance(self.results, tuple) or not self.results:
            raise ValueError("outcome receipt requires a non-empty tuple of results")
        for row in self.results:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                raise ValueError("outcome receipt result rows must contain case id, boolean outcome, and evidence digest")
            case_id, passed, evidence_digest = row
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("outcome receipt contains an invalid case id")
            if type(passed) is not bool:
                raise ValueError("outcome receipt outcomes must be booleans")
            if not isinstance(evidence_digest, str) or not evidence_digest.strip():
                raise ValueError("outcome receipt contains an invalid evidence digest")
        if len({row[0] for row in self.results}) != len(self.results):
            raise ValueError("outcome receipt contains duplicate case ids")
        if not isinstance(self.metrics, tuple):
            raise ValueError("outcome receipt metrics must be a tuple")
        result_ids = {row[0] for row in self.results}
        metric_ids: set[str] = set()
        for row in self.metrics:
            if not isinstance(row, (tuple, list)) or len(row) != 6:
                raise ValueError("metric rows require case id, predicted score, realized score, uncertainty, resource cost, and recovery flag")
            case_id, predicted, realized, uncertainty, resource_cost_ms, recovered = row
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("metric row case_id must be non-empty")
            if case_id not in result_ids:
                raise ValueError("metric row references a case without a result")
            if case_id in metric_ids:
                raise ValueError("outcome receipt contains duplicate metric case ids")
            metric_ids.add(case_id)
            for name, value in (("predicted_score", predicted), ("realized_score", realized), ("uncertainty", uncertainty)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(f"{name} must be finite and within [0,1]")
            if isinstance(resource_cost_ms, bool) or not isinstance(resource_cost_ms, (int, float)) or not math.isfinite(resource_cost_ms) or resource_cost_ms < 0:
                raise ValueError("resource_cost_ms must be finite and non-negative")
            if type(recovered) is not bool:
                raise ValueError("recovered must be a boolean")

    @property
    def receipt_digest(self) -> str:
        payload = {
            "campaign_digest": self.campaign_digest,
            "corpus_digest": self.corpus_digest,
            "run_nonce": self.run_nonce,
            "request_digest": self.request_digest,
            "evaluator_id": self.evaluator_id,
            "oracle_id": self.oracle_id,
            "results": self.results,
            "metrics": self.metrics,
            "contamination_free": self.contamination_free,
            "independent_oracle": self.independent_oracle,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class SealedArenaEvidence:
    receipt_digest: str
    holdout_pass_rate: float
    result_case_count: int
    case_results: tuple[tuple[str, bool, str], ...] = ()
    case_metrics: tuple[tuple[str, float, float, float, float, bool], ...] = ()
    request_digest: str = ""


class SealedArenaBoundary:
    """Accept externally attested outcomes without importing the oracle."""

    def __init__(self, verify_signature) -> None:
        if not callable(verify_signature):
            raise TypeError("verify_signature must be callable")
        self._verify_signature = verify_signature

    def accept(self, request: SealedCampaignRequest, receipt: ExternalOutcomeReceipt) -> SealedArenaEvidence:
        if not isinstance(request, SealedCampaignRequest) or not isinstance(receipt, ExternalOutcomeReceipt):
            raise TypeError("sealed request and externally attested receipt are required")
        if receipt.campaign_digest != request.campaign_digest or receipt.corpus_digest != request.corpus_digest:
            raise ValueError("outcome receipt lineage does not match sealed campaign")
        if receipt.run_nonce != request.run_nonce:
            raise ValueError("outcome receipt nonce does not match sealed campaign request")
        if receipt.request_digest != request.request_digest:
            raise ValueError("outcome receipt request digest does not match the exact sealed case set")
        if receipt.contamination_free is not True or receipt.independent_oracle is not True:
            raise ValueError("sealed Arena rejects contaminated or non-independent outcomes")
        if not self._verify_signature(receipt):
            raise ValueError("external outcome signature is not verified")
        expected_ids = {case.case_id for case in request.cases}
        observed_ids = {row[0] for row in receipt.results}
        if not observed_ids <= expected_ids:
            raise ValueError("outcome receipt contains an unknown case")
        if observed_ids != expected_ids:
            missing = sorted(expected_ids - observed_ids)
            raise ValueError(f"outcome receipt is incomplete; missing cases: {missing}")
        holdouts = {case.case_id for case in request.cases if case.holdout is True}
        holdout_results = [row for row in receipt.results if row[0] in holdouts]
        if len(holdout_results) != len(holdouts):
            raise ValueError("sealed outcome is missing one or more holdout results")
        passed = sum(row[1] is True for row in holdout_results)
        return SealedArenaEvidence(
            receipt.receipt_digest,
            passed / len(holdout_results),
            len(receipt.results),
            tuple(tuple(row) for row in receipt.results),
            tuple(tuple(row) for row in receipt.metrics),
            request.request_digest,
        )


__all__ = ["SealedCaseEnvelope", "SealedCampaignRequest", "ExternalOutcomeReceipt", "SealedArenaEvidence", "SealedArenaBoundary"]
