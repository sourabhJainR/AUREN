"""Candidate-only regression genome with deterministic and independent holdout gates."""
from __future__ import annotations

import re
from typing import Any, Callable

from p1 import stable_id

_ALLOWED_SOURCES = {"accepted_bugfix", "review_finding", "user_correction", "incident"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _safe_text(value: Any, field: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters")
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise ValueError(f"{field} contains credential-like material")
    return value.strip()


def candidate_case(
    source_kind: str,
    source_ref: str,
    trigger: str,
    expected: str,
    constraints: list[str] | None = None,
    *,
    proposer_id: str = "unknown",
) -> dict[str, Any]:
    """Normalize an observed defect/correction as a candidate, never mandatory policy."""
    if not isinstance(source_kind, str) or source_kind not in _ALLOWED_SOURCES:
        raise ValueError("unsupported regression candidate source")
    if constraints is not None and not isinstance(constraints, (list, tuple)):
        raise ValueError("constraints must be a list or tuple")
    source_ref = _safe_text(source_ref, "source_ref", 256)
    trigger = _safe_text(trigger, "trigger")
    expected = _safe_text(expected, "expected")
    proposer_id = _safe_text(proposer_id, "proposer_id", 128)
    normalized_constraints = sorted({
        _safe_text(item, "constraint", 300) for item in (constraints or [])
    })
    identity = "|".join((source_kind, source_ref, trigger, expected, *normalized_constraints))
    return {
        "id": stable_id("reg", identity),
        "source_kind": source_kind,
        "source_ref": source_ref,
        "trigger": trigger,
        "expected": expected,
        "constraints": normalized_constraints,
        "proposer_id": proposer_id,
        "status": "candidate",
        "mandatory": False,
        "promotion_requirements": {
            "deterministic_passes": 2,
            "independent_sealed_holdout": True,
            "human_approval": True,
        },
    }


def evaluate_promotion(
    candidate: dict[str, Any],
    deterministic_runs: list[dict[str, Any]],
    holdout: dict[str, Any] | None,
    approval: dict[str, Any] | None,
    *,
    training_dataset_id: str,
    evidence_verifier: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Return a promotion decision; missing, reused, or non-independent proof fails closed."""
    reasons: list[str] = []
    if not isinstance(candidate, dict):
        return {"case_id": None, "status": "candidate", "mandatory": False,
                "promoted": False, "reasons": ["candidate_invalid"]}
    if not isinstance(deterministic_runs, list):
        deterministic_runs = []
        reasons.append("deterministic_runs_invalid")
    if not isinstance(training_dataset_id, str) or not training_dataset_id:
        reasons.append("training_dataset_required")
    case_id = candidate.get("id")
    expected = candidate.get("expected")
    proposer = candidate.get("proposer_id")
    source_ref = candidate.get("source_ref")
    if (
        not isinstance(case_id, str) or not case_id
        or not isinstance(expected, str) or not expected
        or not isinstance(proposer, str) or not proposer
        or not isinstance(source_ref, str) or not source_ref
    ):
        reasons.append("candidate_identity_invalid")

    def verified(reference: str, evidence_kind: str) -> bool:
        if not callable(evidence_verifier):
            return False
        try:
            return evidence_verifier(reference, evidence_kind) is True
        except Exception:
            return False
    if candidate.get("status") != "candidate" or candidate.get("mandatory") is True:
        reasons.append("candidate_state_invalid")
    if isinstance(source_ref, str) and source_ref and not verified(source_ref, "candidate_source"):
        reasons.append("candidate_source_evidence_not_verified")
    run_ids = [
        item.get("run_id") if isinstance(item, dict) else None
        for item in deterministic_runs
    ]
    if (
        len(deterministic_runs) < 2
        or any(not isinstance(value, str) or not value for value in run_ids)
        or len(set(run_ids)) != len(run_ids)
    ):
        reasons.append("two_distinct_deterministic_runs_required")
    for run in deterministic_runs:
        if (
            not isinstance(run, dict)
            or run.get("case_id") != case_id
            or run.get("passed") is not True
            or run.get("observed") != expected
        ):
            reasons.append("deterministic_regression_failed")
            break
        evidence_ref = run.get("evidence_ref")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
            or not verified(evidence_ref, "deterministic_run")
        ):
            reasons.append("deterministic_run_evidence_not_verified")
            break
    if not isinstance(holdout, dict):
        reasons.append("independent_holdout_required")
    else:
        if holdout.get("case_id") != case_id or holdout.get("passed") is not True:
            reasons.append("holdout_case_failed")
        if holdout.get("sealed") is not True:
            reasons.append("holdout_must_be_sealed")
        dataset_id = holdout.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id or dataset_id == training_dataset_id:
            reasons.append("holdout_dataset_must_be_independent")
        evaluator = holdout.get("evaluator_id")
        if not isinstance(evaluator, str) or not evaluator or evaluator == proposer:
            reasons.append("independent_evaluator_required")
        holdout_ref = holdout.get("evidence_ref")
        if not isinstance(holdout_ref, str) or not holdout_ref.strip():
            reasons.append("holdout_evidence_required")
        elif not verified(holdout_ref, "holdout"):
            reasons.append("holdout_evidence_not_verified")
    if not isinstance(approval, dict) or approval.get("approved") is not True:
        reasons.append("explicit_human_approval_required")
    else:
        reviewer = approval.get("reviewer_id")
        if not isinstance(reviewer, str) or not reviewer or reviewer == proposer:
            reasons.append("independent_reviewer_required")
        if isinstance(holdout, dict) and reviewer == holdout.get("evaluator_id"):
            reasons.append("reviewer_must_be_independent_of_evaluator")
        approval_ref = approval.get("evidence_ref")
        if not isinstance(approval_ref, str) or not approval_ref.strip():
            reasons.append("approval_evidence_required")
        elif not verified(approval_ref, "human_approval"):
            reasons.append("approval_evidence_not_verified")
    promoted = not reasons
    return {
        "case_id": case_id,
        "status": "promoted" if promoted else "candidate",
        "mandatory": promoted,
        "promoted": promoted,
        "reasons": sorted(set(reasons)),
    }
