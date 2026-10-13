"""Explicit acceptance policy for Arena receipts.

Receipt integrity and campaign acceptance are separate decisions. A structurally
valid receipt may contain failed or errored cases; it must not be confused with
a passing benchmark run.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .independent_arena import validate_manifest, verify_receipt


def assess_receipt(
    receipt: Mapping[str, Any],
    sealed: Mapping[str, Any],
    *,
    minimum_overall_pass_rate: float = 0.80,
    minimum_holdout_pass_rate: float = 0.80,
) -> dict[str, Any]:
    """Validate a receipt and apply explicit overall/holdout thresholds.

    This function does not attest that evaluator or corpus custody is independent;
    external signed custody and isolation evidence remain separate prerequisites.
    """
    for name, value in (
        ("minimum_overall_pass_rate", minimum_overall_pass_rate),
        ("minimum_holdout_pass_rate", minimum_holdout_pass_rate),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError(f"{name} must be a finite number between 0 and 1")

    if not verify_receipt(receipt, sealed):
        return {"accepted": False, "reason": "receipt_invalid"}

    manifest = validate_manifest(sealed["manifest"])
    results = receipt["results"]
    total = len(results)
    passed = receipt["counts"]["pass"]
    errors = receipt["counts"]["error"]
    holdout_rows = [
        row for row in results if row["split"] == "sealed_holdout"
    ]
    if total == 0 or not holdout_rows:
        return {
            "accepted": False,
            "reason": "required_cases_missing",
            "case_count": total,
            "holdout_case_count": len(holdout_rows),
        }

    holdout_passed = sum(row["outcome"] == "pass" for row in holdout_rows)
    overall_rate = passed / total
    holdout_rate = holdout_passed / len(holdout_rows)
    reasons = []
    if errors:
        reasons.append("case_errors_present")
    if overall_rate < minimum_overall_pass_rate:
        reasons.append("overall_threshold_not_met")
    if holdout_rate < minimum_holdout_pass_rate:
        reasons.append("holdout_threshold_not_met")

    # Manifest validation is deliberately performed before reporting acceptance,
    # ensuring the receipt is assessed against the evaluator-owned sealed corpus.
    _ = manifest
    return {
        "accepted": not reasons,
        "reason": "accepted" if not reasons else ",".join(reasons),
        "case_count": total,
        "pass_count": passed,
        "error_count": errors,
        "holdout_case_count": len(holdout_rows),
        "holdout_pass_count": holdout_passed,
        "overall_pass_rate": overall_rate,
        "holdout_pass_rate": holdout_rate,
    }


__all__ = ["assess_receipt"]
