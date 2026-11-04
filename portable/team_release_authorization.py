"""Independent, evidence-bound release authorization for supervised teams.

This module evaluates signed QA, security, workspace, provider/license, and release
approvals. The host must verify signatures and persist the resulting authorization
through the canonical approval/release store before it can perform a deployment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence


class TeamReleaseAuthorizationError(ValueError):
    """Release authorization evidence is malformed or does not match the candidate."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: Mapping[str, Any], *, omit: tuple[str, ...] = ()) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in value.items() if k not in omit})).hexdigest()


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise TeamReleaseAuthorizationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        raise TeamReleaseAuthorizationError(f"{field} must be non-empty bounded text")
    return value.strip()


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TeamReleaseAuthorizationError(f"{field} must be an ISO-8601 timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TeamReleaseAuthorizationError(f"{field} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise TeamReleaseAuthorizationError(f"{field} must include a timezone")
    return result.astimezone(timezone.utc)


_APPROVAL_FIELDS = {
    "schema_version", "receipt_id", "role_id", "principal_id", "task_id",
    "run_id", "artifact_digest", "commit_sha", "plan_digest", "decision",
    "issued_at", "expires_at", "evidence_digest", "signature",
}
_WORKSPACE_FIELDS = {
    "schema_version", "task_id", "run_id", "artifact_digest", "commit_sha",
    "plan_digest", "workspace_id", "sandbox_id", "isolation_profile",
    "workspace_digest", "status", "issued_at", "evidence_digest", "signature",
}


@dataclass(frozen=True, slots=True)
class TeamReleaseAuthorization:
    status: str
    authorized: bool
    task_id: str
    run_id: str
    artifact_digest: str
    commit_sha: str
    plan_digest: str
    authorization_digest: str | None
    authorization_record_id: str | None
    blocking_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "auren-team-release-authorization-v1",
            "status": self.status,
            "authorized": self.authorized,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "artifact_digest": self.artifact_digest,
            "commit_sha": self.commit_sha,
            "plan_digest": self.plan_digest,
            "authorization_digest": self.authorization_digest,
            "authorization_record_id": self.authorization_record_id,
            "blocking_reasons": list(self.blocking_reasons),
        }


def authorize_team_release(
    *,
    readiness: Mapping[str, Any],
    task_id: str,
    run_id: str,
    artifact_digest: str,
    commit_sha: str,
    plan_digest: str,
    implementer_principal_ids: Sequence[str],
    provider_reports: Sequence[Mapping[str, Any]],
    workspace_receipt: Mapping[str, Any] | None,
    qa_receipt: Mapping[str, Any] | None,
    security_receipt: Mapping[str, Any] | None,
    release_receipt: Mapping[str, Any] | None,
    release_approver_role_id: str,
    verify_approval: Callable[[Mapping[str, Any]], bool] | None,
    verify_workspace_receipt: Callable[[Mapping[str, Any]], bool] | None,
    persist_authorization: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    now: datetime | None = None,
    max_receipt_age_seconds: int = 24 * 60 * 60,
) -> TeamReleaseAuthorization:
    """Authorize only the exact candidate reviewed by independent trusted principals.

    verify_approval and verify_workspace_receipt must be host-owned signature and
    custody verifiers. persist_authorization must append the authorization to the
    canonical durable approval/release ledger and return its committed record ID and
    digest. Without all three host capabilities this function cannot authorize.
    """
    task_id = _text(task_id, "task_id")
    run_id = _text(run_id, "run_id")
    release_approver_role_id = _text(release_approver_role_id, "release_approver_role_id")
    artifact_digest = _sha256(artifact_digest, "artifact_digest")
    plan_digest = _sha256(plan_digest, "plan_digest")
    if not isinstance(commit_sha, str) or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
        raise TeamReleaseAuthorizationError("commit_sha must be a full lowercase commit SHA")
    if type(max_receipt_age_seconds) is not int or max_receipt_age_seconds < 1:
        raise TeamReleaseAuthorizationError("max_receipt_age_seconds must be a positive integer")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons: list[str] = []

    if not isinstance(readiness, Mapping) or readiness.get("task_id") != task_id or readiness.get("run_id") != run_id:
        reasons.append("canonical_team_readiness_missing_or_mismatched")
    else:
        if readiness.get("delivery_ready") is not True or readiness.get("release_ready") is not True:
            reasons.append("canonical_team_delivery_or_review_not_ready")
        if readiness.get("release_authorized") is not False:
            reasons.append("canonical_release_authorization_state_invalid")
        if readiness.get("blocking_reasons"):
            reasons.append("canonical_team_readiness_has_blockers")

    implementers = tuple(dict.fromkeys(
        _text(item, "implementer_principal_id") for item in implementer_principal_ids
    ))
    if not implementers:
        reasons.append("implementer_identity_missing")

    if not provider_reports:
        reasons.append("provider_readiness_evidence_missing")
    for report in provider_reports:
        if not isinstance(report, Mapping):
            reasons.append("provider_readiness_report_malformed")
            continue
        if (
            report.get("schema_version") != "auren-provider-readiness-v1"
            or report.get("task_id") != task_id
            or report.get("run_id") != run_id
            or report.get("artifact_digest") != artifact_digest
            or report.get("plan_digest") != plan_digest
            or report.get("status") != "passed"
            or report.get("ready") is not True
        ):
            reasons.append("provider_or_license_readiness_not_passed_for_candidate")
        try:
            _sha256(report.get("evidence_digest"), "provider readiness evidence_digest")
        except TeamReleaseAuthorizationError:
            reasons.append("provider_readiness_evidence_digest_invalid")

    if workspace_receipt is None:
        reasons.append("host_isolation_receipt_missing")
    else:
        try:
            if set(workspace_receipt) != _WORKSPACE_FIELDS:
                raise TeamReleaseAuthorizationError("workspace receipt fields are invalid")
            workspace = dict(workspace_receipt)
            if workspace["schema_version"] != "auren-workspace-isolation-v1":
                raise TeamReleaseAuthorizationError("workspace isolation schema is unsupported")
            for key, expected in (
                ("task_id", task_id), ("run_id", run_id),
                ("artifact_digest", artifact_digest), ("commit_sha", commit_sha),
                ("plan_digest", plan_digest),
            ):
                if workspace[key] != expected:
                    raise TeamReleaseAuthorizationError(f"workspace receipt {key} mismatch")
            if workspace["status"] != "passed":
                reasons.append("host_workspace_isolation_not_passed")
            for key in ("workspace_id", "sandbox_id", "isolation_profile"):
                _text(workspace[key], key)
            _sha256(workspace["workspace_digest"], "workspace_digest")
            issued = _time(workspace["issued_at"], "workspace issued_at")
            if issued > current + timedelta(seconds=30) or (current - issued).total_seconds() > max_receipt_age_seconds:
                reasons.append("workspace_isolation_receipt_stale_or_future")
            evidence_digest = _sha256(workspace["evidence_digest"], "workspace evidence_digest")
            if evidence_digest != _digest(workspace, omit=("evidence_digest", "signature")):
                raise TeamReleaseAuthorizationError("workspace evidence digest mismatch")
            if not isinstance(workspace["signature"], str) or not workspace["signature"].strip():
                raise TeamReleaseAuthorizationError("workspace signature is missing")
            if not callable(verify_workspace_receipt) or verify_workspace_receipt(workspace) is not True:
                raise TeamReleaseAuthorizationError("workspace isolation receipt is not host-authenticated")
        except Exception:
            reasons.append("host_workspace_isolation_evidence_invalid")

    def validate_approval(receipt: Mapping[str, Any] | None, *, role_id: str, label: str) -> str | None:
        if receipt is None:
            reasons.append(f"{label}_approval_missing")
            return None
        try:
            if not isinstance(receipt, Mapping) or set(receipt) != _APPROVAL_FIELDS:
                raise TeamReleaseAuthorizationError("approval receipt fields are invalid")
            item = dict(receipt)
            if item["schema_version"] != "auren-independent-approval-v1":
                raise TeamReleaseAuthorizationError("approval receipt schema is unsupported")
            if item["role_id"] != role_id:
                raise TeamReleaseAuthorizationError("approval role mismatch")
            if item["task_id"] != task_id or item["run_id"] != run_id:
                raise TeamReleaseAuthorizationError("approval task/run mismatch")
            if item["artifact_digest"] != artifact_digest or item["commit_sha"] != commit_sha or item["plan_digest"] != plan_digest:
                raise TeamReleaseAuthorizationError("approval candidate binding mismatch")
            if item["decision"] != "approved":
                raise TeamReleaseAuthorizationError("approval decision is not approved")
            principal = _text(item["principal_id"], f"{label} principal_id")
            _text(item["receipt_id"], f"{label} receipt_id")
            issued = _time(item["issued_at"], f"{label} issued_at")
            expires = _time(item["expires_at"], f"{label} expires_at")
            if issued > current + timedelta(seconds=30) or (current - issued).total_seconds() > max_receipt_age_seconds or expires <= current:
                raise TeamReleaseAuthorizationError("approval receipt is stale, future-dated, or expired")
            digest = _sha256(item["evidence_digest"], f"{label} evidence_digest")
            if digest != _digest(item, omit=("evidence_digest", "signature")):
                raise TeamReleaseAuthorizationError("approval evidence digest mismatch")
            if not isinstance(item["signature"], str) or not item["signature"].strip():
                raise TeamReleaseAuthorizationError("approval signature is missing")
            if not callable(verify_approval) or verify_approval(item) is not True:
                raise TeamReleaseAuthorizationError("approval signature is not trusted")
            return principal
        except Exception:
            reasons.append(f"{label}_approval_invalid")
            return None

    qa_principal = validate_approval(qa_receipt, role_id="QA-01", label="qa")
    security_principal = validate_approval(security_receipt, role_id="SEC-01", label="security")
    release_principal = validate_approval(
        release_receipt, role_id=release_approver_role_id, label="release",
    )
    principals = [item for item in (qa_principal, security_principal, release_principal) if item]
    if len(principals) != 3 or len(set(principals)) != 3:
        reasons.append("qa_security_and_release_approvers_must_be_distinct")
    if any(principal in implementers for principal in principals):
        reasons.append("implementer_cannot_approve_or_authorize_release")

    if reasons:
        return TeamReleaseAuthorization(
            "blocked", False, task_id, run_id, artifact_digest, commit_sha,
            plan_digest, None, None, tuple(dict.fromkeys(reasons)),
        )

    authorization_payload = {
        "schema_version": "auren-team-release-authorization-v1",
        "task_id": task_id,
        "run_id": run_id,
        "artifact_digest": artifact_digest,
        "commit_sha": commit_sha,
        "plan_digest": plan_digest,
        "provider_evidence_digests": sorted(
            str(report["evidence_digest"]) for report in provider_reports
        ),
        "workspace_evidence_digest": str(workspace_receipt["evidence_digest"]),
        "qa_receipt_id": str(qa_receipt["receipt_id"]),
        "security_receipt_id": str(security_receipt["receipt_id"]),
        "release_receipt_id": str(release_receipt["receipt_id"]),
        "reviewer_principals": sorted(principals),
        "implementer_principals": sorted(implementers),
        "authorized_at": current.isoformat(),
    }
    authorization_digest = hashlib.sha256(_canonical(authorization_payload)).hexdigest()
    record_payload = {**authorization_payload, "authorization_digest": authorization_digest}
    if not callable(persist_authorization):
        return TeamReleaseAuthorization(
            "blocked", False, task_id, run_id, artifact_digest, commit_sha,
            plan_digest, authorization_digest, None,
            ("canonical_release_authorization_persistence_unavailable",),
        )
    try:
        persisted = persist_authorization(record_payload)
    except Exception:
        persisted = None
    if (
        not isinstance(persisted, Mapping)
        or persisted.get("persisted") is not True
        or not isinstance(persisted.get("record_id"), str)
        or not persisted["record_id"].strip()
        or persisted.get("authorization_digest") != authorization_digest
        or not isinstance(persisted.get("record_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", persisted["record_digest"]) is None
    ):
        return TeamReleaseAuthorization(
            "blocked", False, task_id, run_id, artifact_digest, commit_sha,
            plan_digest, authorization_digest, None,
            ("canonical_release_authorization_persistence_unverified",),
        )
    return TeamReleaseAuthorization(
        "authorized", True, task_id, run_id, artifact_digest, commit_sha,
        plan_digest, authorization_digest, persisted["record_id"], (),
    )


__all__ = [
    "TeamReleaseAuthorization", "TeamReleaseAuthorizationError",
    "authorize_team_release",
]
