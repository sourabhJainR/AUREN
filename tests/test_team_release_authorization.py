from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from portable.team_release_authorization import authorize_team_release


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
ARTIFACT = "a" * 64
PLAN = "b" * 64
COMMIT = "1" * 40


def digest(record):
    payload = {key: value for key, value in record.items() if key not in {"evidence_digest", "signature"}}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def approval(role, principal, receipt_id):
    record = {
        "schema_version": "auren-independent-approval-v1",
        "receipt_id": receipt_id,
        "role_id": role,
        "principal_id": principal,
        "task_id": "task-1",
        "run_id": "run-1",
        "artifact_digest": ARTIFACT,
        "commit_sha": COMMIT,
        "plan_digest": PLAN,
        "decision": "approved",
        "issued_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=2)).isoformat(),
        "evidence_digest": "",
        "signature": "host-signed-test-fixture",
    }
    record["evidence_digest"] = digest(record)
    return record


def workspace():
    record = {
        "schema_version": "auren-workspace-isolation-v1",
        "task_id": "task-1",
        "run_id": "run-1",
        "artifact_digest": ARTIFACT,
        "commit_sha": COMMIT,
        "plan_digest": PLAN,
        "workspace_id": "workspace-1",
        "sandbox_id": "sandbox-1",
        "isolation_profile": "restricted-egress-no-secrets-v1",
        "workspace_digest": "c" * 64,
        "status": "passed",
        "issued_at": NOW.isoformat(),
        "evidence_digest": "",
        "signature": "host-signed-test-fixture",
    }
    record["evidence_digest"] = digest(record)
    return record


def provider_report():
    return {
        "schema_version": "auren-provider-readiness-v1",
        "provider_id": "market-data-a",
        "capability": "quotes",
        "intended_use": "commercial_market_display",
        "task_id": "task-1",
        "run_id": "run-1",
        "artifact_digest": ARTIFACT,
        "plan_digest": PLAN,
        "status": "passed",
        "ready": True,
        "blocking_reasons": [],
        "live_probe_digest": "d" * 64,
        "license_evidence_digest": "e" * 64,
        "evidence_digest": "f" * 64,
    }


def authorize(*, qa=None, security=None, release=None, readiness=None,
              provider_reports=None, workspace_receipt=None, verifier=None,
              persistence=None):
    return authorize_team_release(
        readiness_resolver=lambda _task, _run: readiness or {
            "task_id": "task-1", "run_id": "run-1",
            "delivery_ready": True, "release_ready": True,
            "release_authorized": False, "blocking_reasons": [],
        },
        implementer_principals_resolver=lambda _task, _run: ["implementer-a", "implementer-b"],
        provider_reports_resolver=lambda _task, _run, _artifact, _plan: provider_reports if provider_reports is not None else [provider_report()],
        verify_provider_report=lambda _report: True,
        task_id="task-1", run_id="run-1",
        artifact_digest=ARTIFACT, commit_sha=COMMIT, plan_digest=PLAN,
        workspace_receipt=workspace_receipt if workspace_receipt is not None else workspace(),
        qa_receipt=qa if qa is not None else approval("QA-01", "qa-a", "qa-1"),
        security_receipt=security if security is not None else approval("SEC-01", "security-a", "sec-1"),
        release_receipt=release if release is not None else approval("CTO-01", "release-a", "release-1"),
        release_approver_role_id="CTO-01",
        verify_approval=verifier or (lambda _record: True),
        verify_workspace_receipt=verifier or (lambda _record: True),
        persist_authorization=persistence or (lambda payload: {
            "persisted": True, "record_id": "release-record-1",
            "authorization_digest": payload["authorization_digest"],
            "record_digest": hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            ).encode()).hexdigest(),
        }),
        now=NOW,
    )


def test_release_requires_distinct_qa_security_and_release_authorities():
    decision = authorize()
    assert decision.authorized is True
    assert decision.status == "authorized"
    assert decision.authorization_record_id == "release-record-1"


def test_implementer_cannot_approve_and_reviewers_must_be_distinct():
    duplicate = approval("SEC-01", "qa-a", "sec-1")
    decision = authorize(security=duplicate)
    assert decision.authorized is False
    assert "qa_security_and_release_approvers_must_be_distinct" in decision.blocking_reasons
    assert "implementer_cannot_approve_or_authorize_release" not in decision.blocking_reasons


def test_candidate_revision_and_provider_evidence_are_bound():
    report = provider_report()
    report["artifact_digest"] = "2" * 64
    assert authorize(provider_reports=[report]).authorized is False
    assert authorize(readiness={
        "task_id": "task-1", "run_id": "run-1",
        "delivery_ready": True, "release_ready": False,
        "release_authorized": False, "blocking_reasons": ["security"],
    }).authorized is False


def test_unverified_workspace_and_approval_receipts_block_release():
    assert authorize(verifier=lambda _record: False).authorized is False
    broken = workspace()
    broken["workspace_digest"] = "x" * 64
    assert authorize(workspace_receipt=broken).authorized is False


def test_release_is_not_authorized_until_canonical_persistence_is_confirmed():
    result = authorize(persistence=lambda _payload: {"persisted": False})
    assert result.authorized is False
    assert result.authorization_record_id is None
