"""Fail-closed validation for consequential engineering decision records.

This validator consumes a task-local record. It is not an evidence store, trust
score, permission system, or substitute for repository branch protection.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping
import re

SCHEMA_VERSION = "1.0"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROMOTION_LEVELS = {
    "none": 0,
    "provisional": 1,
    "verified_for_scope": 2,
    "repeatedly_reliable": 3,
    "independently_corroborated": 4,
}
CHECK_STATUSES = {"passed", "failed", "skipped", "unknown"}


def validate_decision_record(
    record: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head: str,
    authorized_approvers: Iterable[str] = (),
) -> list[str]:
    """Return contract violations; an empty list means structurally eligible.

    expected_repository and expected_head must come from live repository
    context, not from the record being validated.
    """
    errors: list[str] = []

    def required_text(key: str, where: Mapping[str, Any] = record) -> str:
        value = where.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} is required")
            return ""
        return value.strip()

    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported or missing schema_version")
    required_text("task_id")
    repository = required_text("repository")
    base_commit = required_text("base_commit")
    head_commit = required_text("head_commit")
    required_text("selected_option")
    required_text("selection_rationale")
    required_text("rollback_trigger")
    required_text("rollback_action")
    risk_level = required_text("risk_level")

    if not expected_repository.strip():
        errors.append("trusted expected_repository context is required")
    if not COMMIT_RE.fullmatch(expected_head):
        errors.append("trusted expected_head must be a full 40-character commit SHA")
    if repository and repository != expected_repository:
        errors.append("record repository does not match live repository context")
    if not COMMIT_RE.fullmatch(base_commit):
        errors.append("base_commit must be a full 40-character commit SHA")
    if not COMMIT_RE.fullmatch(head_commit):
        errors.append("head_commit must be a full 40-character commit SHA")
    if head_commit and head_commit != expected_head:
        errors.append("decision evidence is stale: head_commit does not match expected head")
    if risk_level not in {"low", "medium", "high", "critical"}:
        errors.append("risk_level must be low, medium, high, or critical")

    criteria = record.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
        errors.append("acceptance_criteria must be a non-empty list of non-empty strings")

    dependencies = record.get("dependencies")
    if not isinstance(dependencies, list) or any(not isinstance(x, str) or not x.strip() for x in dependencies):
        errors.append("dependencies must be a list of non-empty strings")

    alternatives = record.get("alternatives")
    if not isinstance(alternatives, list):
        errors.append("alternatives must be a list")
    else:
        for i, item in enumerate(alternatives):
            if not isinstance(item, dict) or not isinstance(item.get("option"), str) or not item["option"].strip() or not isinstance(item.get("reason_rejected"), str) or not item["reason_rejected"].strip():
                errors.append(f"alternatives[{i}] requires option and reason_rejected")
        if not alternatives and risk_level in {"medium", "high", "critical"}:
            errors.append("non-trivial decisions must record at least one alternative")

    limitations = record.get("limitations")
    if not isinstance(limitations, list) or any(not isinstance(x, str) or not x.strip() for x in limitations):
        errors.append("limitations must be a list of non-empty strings")

    checks = record.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must contain at least one check")
        checks = []
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"checks[{i}] must be an object")
            continue
        for key in ("name", "command", "evidence_id", "commit_sha"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                errors.append(f"checks[{i}].{key} is required")
        if check.get("status") not in CHECK_STATUSES:
            errors.append(f"checks[{i}].status is invalid")
        if check.get("status") == "passed" and check.get("commit_sha") != expected_head:
            errors.append(f"checks[{i}] passed evidence is not bound to expected head")
        evidence_id = check.get("evidence_id")
        if check.get("status") == "passed" and (not isinstance(evidence_id, str) or not evidence_id.strip()):
            errors.append(f"checks[{i}] passed evidence requires evidence_id")

    retries = record.get("retry_history", [])
    if not isinstance(retries, list):
        errors.append("retry_history must be a list")
        retries = []
    previous_signature: tuple[Any, Any] | None = None
    seen_retry_evidence: set[str] = set()
    for i, attempt in enumerate(retries):
        if not isinstance(attempt, dict):
            errors.append(f"retry_history[{i}] must be an object")
            continue
        for key in ("hypothesis", "strategy"):
            if not isinstance(attempt.get(key), str) or not attempt[key].strip():
                errors.append(f"retry_history[{i}].{key} is required")
        evidence_added = attempt.get("new_evidence_ids", [])
        if not isinstance(evidence_added, list) or any(not isinstance(x, str) or not x.strip() for x in evidence_added):
            errors.append(f"retry_history[{i}].new_evidence_ids must be a list of non-empty strings")
            evidence_added = []
        signature = (attempt.get("hypothesis"), attempt.get("strategy"))
        if previous_signature is not None and signature == previous_signature and not set(evidence_added).difference(seen_retry_evidence):
            errors.append(f"retry_history[{i}] is a no-progress retry; change hypothesis/strategy or add new evidence")
        previous_signature = signature
        seen_retry_evidence.update(evidence_added)

    promotion = record.get("promotion")
    if not isinstance(promotion, dict):
        errors.append("promotion object is required")
        promotion = {}
    level = promotion.get("level", "none")
    if level not in PROMOTION_LEVELS:
        errors.append("promotion.level is invalid")
        level = "none"
    if PROMOTION_LEVELS[level] > 0:
        if any(not isinstance(check, dict) or check.get("status") != "passed" for check in checks):
            errors.append("promotion is blocked unless every recorded check passed")
        if not checks or any(isinstance(check, dict) and check.get("commit_sha") != expected_head for check in checks):
            errors.append("promotion is blocked by missing or mismatched exact-head evidence")
        approver = promotion.get("approved_by")
        approval_evidence = promotion.get("approval_evidence_id")
        approved = set(authorized_approvers)
        if not isinstance(approver, str) or not approver.strip() or approver not in approved:
            errors.append("promotion requires an explicitly authorized approver")
        if not isinstance(approval_evidence, str) or not approval_evidence.strip():
            errors.append("promotion requires approval_evidence_id")
    if level == "independently_corroborated":
        for key in ("external_evaluator_id", "corpus_owner_id", "receipt_digest", "repeat_run_id"):
            if not isinstance(promotion.get(key), str) or not promotion[key].strip():
                errors.append(f"independent corroboration requires promotion.{key}")
        if promotion.get("evaluator_control") in {None, "", "implementation_team"}:
            errors.append("independent corroboration requires a separately controlled evaluator")
        if promotion.get("corpus_control") in {None, "", "implementation_team"}:
            errors.append("independent corroboration requires a separately controlled corpus/oracle")

    return errors


def require_valid_decision_record(
    record: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head: str,
    authorized_approvers: Iterable[str] = (),
) -> None:
    """Raise ValueError on any contract violation."""
    errors = validate_decision_record(
        record,
        expected_repository=expected_repository,
        expected_head=expected_head,
        authorized_approvers=authorized_approvers,
    )
    if errors:
        raise ValueError("decision record rejected: " + "; ".join(errors))


__all__ = ["PROMOTION_LEVELS", "SCHEMA_VERSION", "require_valid_decision_record", "validate_decision_record"]
