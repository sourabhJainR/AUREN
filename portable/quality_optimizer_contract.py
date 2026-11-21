"""Trust-boundary adapters for quality rubric and optimizer evidence.

These helpers add verification around the descriptive rubric and optimizer
primitives. They do not grant dispatch, merge, promotion, or Guard authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

from .evaluation_rubric import (
    RubricDefinition,
    RubricScoreReceipt,
    evaluate_rubric,
    rubric_plan_binding,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_plan_rubric_binding(
    rubric: RubricDefinition,
    binding: Mapping[str, Any],
    *,
    plan_id: str,
    plan_digest: str,
    task_ids: Sequence[str],
) -> None:
    """Reject missing, stale, altered, or differently scoped plan/rubric pins."""
    expected = rubric_plan_binding(
        rubric, plan_id=plan_id, plan_digest=plan_digest, task_ids=task_ids
    )
    if not isinstance(binding, Mapping):
        raise TypeError("canonical plan must contain a rubric binding")
    supplied = dict(binding)
    if supplied != expected:
        raise ValueError("canonical plan rubric binding is missing, stale, or altered")


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evaluator and executor identities are required")
    # Identity comparison is deliberately conservative: case/whitespace aliases
    # must not let the same principal appear independent.
    return " ".join(value.casefold().split())


def evaluate_verified_rubric(
    rubric: RubricDefinition,
    *,
    evidence_resolver: Callable[[str], Mapping[str, Any] | None],
    expected_run_id: str,
    expected_source_digest: str,
    expected_artifact_digests: Sequence[str],
    plan_binding: Mapping[str, Any],
    plan_id: str,
    task_ids: Sequence[str],
    executor_id: str,
    evaluator_id: str,
    **evaluation: Any,
) -> RubricScoreReceipt:
    """Evaluate only evidence objects resolved and bound to this exact run.

    Resolver records must expose: evidence_id, run_id, source_digest,
    artifact_digest, evidence_type, verified, and custodian_id. A URI or
    caller-provided string alone is never accepted as evidence.
    """
    if not callable(evidence_resolver):
        raise TypeError("evidence_resolver must resolve canonical evidence objects")
    if _identity(executor_id) == _identity(evaluator_id):
        raise ValueError("executor and evaluator resolve to the same principal")
    if not _SHA256.fullmatch(expected_source_digest):
        raise ValueError("expected_source_digest must be a SHA-256 digest")
    allowed_artifacts = set(expected_artifact_digests)
    if not allowed_artifacts or any(not _SHA256.fullmatch(x) for x in allowed_artifacts):
        raise ValueError("expected artifact digests must be SHA-256 values")

    plan_digest = evaluation.get("plan_digest")
    if not isinstance(plan_digest, str):
        raise ValueError("evaluation plan_digest is required")
    verify_plan_rubric_binding(
        rubric, plan_binding, plan_id=plan_id, plan_digest=plan_digest, task_ids=task_ids
    )
    evidence = evaluation.pop("evidence")
    if evaluation.get("run_id") != expected_run_id:
        raise ValueError("evaluation run_id does not match verified evidence run")
    if evaluation.get("source_digest") not in (None, expected_source_digest):
        raise ValueError("evaluation source digest does not match verified evidence")
    verified: dict[str, list[str]] = {}
    for criterion_id, references in evidence.items():
        verified_refs: list[str] = []
        for reference in references:
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError("evidence references must be non-empty canonical IDs")
            record = evidence_resolver(reference)
            if not isinstance(record, Mapping):
                raise ValueError(f"evidence object does not resolve: {reference}")
            required = {
                "evidence_id", "run_id", "source_digest", "artifact_digest",
                "evidence_type", "verified", "custodian_id",
            }
            if not required.issubset(record):
                raise ValueError(f"evidence object is missing trust metadata: {reference}")
            if record["evidence_id"] != reference:
                raise ValueError("evidence resolver returned a different evidence ID")
            if record["run_id"] != expected_run_id:
                raise ValueError("evidence belongs to a different run")
            if record["source_digest"] != expected_source_digest:
                raise ValueError("evidence source digest does not match evaluated source")
            if record["artifact_digest"] not in allowed_artifacts:
                raise ValueError("evidence artifact is outside the evaluated artifact set")
            if record["verified"] is not True:
                raise ValueError("evidence object is not independently verified")
            custodian = _identity(str(record["custodian_id"]))
            if custodian in {_identity(executor_id), _identity(evaluator_id)}:
                raise ValueError("evidence custodian must be independent of executor and evaluator")
            if not isinstance(record["evidence_type"], str) or not record["evidence_type"].strip():
                raise ValueError("evidence type is required")
            verified_refs.append(record["evidence_type"].strip() + "://" + reference)
        verified[criterion_id] = verified_refs

    return evaluate_rubric(
        rubric,
        evidence=verified,
        executor_id=executor_id,
        evaluator_id=evaluator_id,
        source_digest=expected_source_digest,
        artifact_digests=tuple(sorted(allowed_artifacts)),
        **evaluation,
    )


@dataclass(frozen=True, slots=True)
class ProviderUsageReceipt:
    """Measured provider usage; estimates must never be passed as receipts."""

    provider: str
    model: str
    provider_version: str
    request_id: str
    task_digest: str
    context_digest: str
    policy_digest: str
    rubric_digest: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    latency_ms: float
    retry_count: int
    tool_call_count: int
    price_revision: str | None = None
    input_cost: float | None = None
    output_cost: float | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model", "provider_version", "request_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("task_digest", "context_digest", "policy_digest", "rubric_digest"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be a SHA-256 digest")
        for name in ("input_tokens", "output_tokens", "cached_input_tokens",
                     "retry_count", "tool_call_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens cannot exceed input tokens")
        if type(self.latency_ms) not in (int, float) or not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("latency_ms must be finite and non-negative")
        for name in ("input_cost", "output_cost"):
            value = getattr(self, name)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative when supplied")
        if (self.input_cost is not None or self.output_cost is not None) and not self.price_revision:
            raise ValueError("priced usage requires a price_revision")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> float | None:
        if self.input_cost is None or self.output_cost is None:
            return None
        return self.input_cost + self.output_cost

    @property
    def receipt_digest(self) -> str:
        return _digest({
            "provider": self.provider, "model": self.model,
            "provider_version": self.provider_version, "request_id": self.request_id,
            "task_digest": self.task_digest, "context_digest": self.context_digest,
            "policy_digest": self.policy_digest, "rubric_digest": self.rubric_digest,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "latency_ms": self.latency_ms, "retry_count": self.retry_count,
            "tool_call_count": self.tool_call_count, "price_revision": self.price_revision,
            "input_cost": self.input_cost, "output_cost": self.output_cost,
        })


def verify_paired_run_identity(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    """Require identical experimental identity before comparing optimization outcomes."""
    dimensions = (
        "task_digest", "source_digest", "context_digest", "tool_digest",
        "provider_version", "model_tier", "model_id", "rubric_digest",
        "policy_digest", "privacy_scope_digest", "acceptance_digest",
        "context_budget_tokens",
    )
    missing = [key for key in dimensions if key not in baseline or key not in candidate]
    if missing:
        raise ValueError("paired runs are missing identity fields: " + ", ".join(missing))
    changed = [key for key in dimensions if baseline[key] != candidate[key]]
    if changed:
        raise ValueError("paired runs are not comparable; identity differs: " + ", ".join(changed))


__all__ = [
    "ProviderUsageReceipt",
    "evaluate_verified_rubric",
    "verify_paired_run_identity",
    "verify_plan_rubric_binding",
]
