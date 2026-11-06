from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from portable.provider_readiness import evaluate_provider_readiness


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
ARTIFACT = "a" * 64
PLAN = "b" * 64


def digest(record, excluded=("evidence_digest",)):
    payload = {key: value for key, value in record.items() if key not in excluded}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def evidence(*, observed_at=None, reviewed_at=None, expires_at=None,
             redistribution=True, commercial=True, storage=True,
             permitted_uses=None):
    probe = {
        "schema_version": "auren-provider-live-probe-v1",
        "provider_id": "market-data-a",
        "capability": "quotes",
        "challenge_id": "fresh-challenge",
        "status": "passed",
        "observed_at": (observed_at or NOW).isoformat(),
        "latency_ms": 120,
        "provider_version": "2026.11",
        "adapter_id": "market-data-adapter-v2",
        "evidence_digest": "",
    }
    probe["evidence_digest"] = digest(probe)
    license_record = {
        "schema_version": "auren-provider-license-v1",
        "provider_id": "market-data-a",
        "intended_use": "commercial_market_display",
        "terms_uri": "https://provider.example/terms",
        "terms_digest": "c" * 64,
        "reviewed_at": (reviewed_at or NOW).isoformat(),
        "expires_at": (expires_at or NOW + timedelta(days=30)).isoformat(),
        "reviewer_principal_id": "license-reviewer",
        "approved": True,
        "permitted_uses": permitted_uses or ["commercial_market_display"],
        "redistribution_allowed": redistribution,
        "commercial_use_allowed": commercial,
        "storage_allowed": storage,
        "evidence_digest": "",
    }
    license_record["evidence_digest"] = digest(license_record)
    return probe, license_record


def evaluate(probe, license_record, **kwargs):
    return evaluate_provider_readiness(
        provider_id="market-data-a",
        capability="quotes",
        intended_use="commercial_market_display",
        task_id="task-1",
        run_id="run-1",
        artifact_digest=ARTIFACT,
        plan_digest=PLAN,
        challenge_id="fresh-challenge",
        live_probe=probe,
        license_evidence=license_record,
        verify_live_probe=lambda _record: True,
        verify_license_evidence=lambda _record: True,
        now=NOW,
        requires_redistribution=True,
        requires_commercial_use=True,
        requires_storage=True,
        **kwargs,
    )


def test_live_provider_and_exact_license_evidence_pass():
    probe, license_record = evidence()
    report = evaluate(probe, license_record)
    assert report.status == "passed"
    assert report.ready is True
    assert report.as_dict()["evidence_digest"] == report.evidence_digest


def test_missing_live_probe_or_license_blocks_readiness():
    probe, license_record = evidence()
    assert evaluate(None, license_record).status == "blocked"
    assert evaluate(probe, None).status == "blocked"


def test_replayed_challenge_and_stale_probe_are_not_live_validation():
    probe, license_record = evidence(observed_at=NOW - timedelta(hours=1))
    assert evaluate(probe, license_record).status == "blocked"
    probe, license_record = evidence()
    probe["challenge_id"] = "old-challenge"
    probe["evidence_digest"] = digest(probe)
    assert evaluate(probe, license_record).status == "failed"


def test_untrusted_or_tampered_receipts_fail_closed():
    probe, license_record = evidence()
    report = evaluate_provider_readiness(
        provider_id="market-data-a", capability="quotes",
        intended_use="commercial_market_display", task_id="task-1", run_id="run-1",
        artifact_digest=ARTIFACT, plan_digest=PLAN, challenge_id="fresh-challenge",
        live_probe=probe, license_evidence=license_record,
        verify_live_probe=lambda _record: False,
        verify_license_evidence=lambda _record: True, now=NOW,
    )
    assert report.status == "failed"
    license_record["terms_digest"] = "d" * 64
    assert evaluate(probe, license_record).status == "failed"


def test_license_scope_and_expiry_are_mandatory():
    probe, license_record = evidence(redistribution=False)
    assert evaluate(probe, license_record).status == "blocked"
    probe, license_record = evidence(expires_at=NOW - timedelta(seconds=1))
    assert evaluate(probe, license_record).status == "blocked"
    probe, license_record = evidence(permitted_uses=["internal_research"])
    assert evaluate(probe, license_record).status == "blocked"


def test_credentials_and_unknown_fields_are_not_accepted_in_evidence():
    probe, license_record = evidence()
    probe["api_key"] = "must-not-be-recorded"
    report = evaluate(probe, license_record)
    assert report.status == "failed"
    assert "must-not-be-recorded" not in str(report.as_dict())
