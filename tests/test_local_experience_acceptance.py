from __future__ import annotations

import json

from portable import local_acceptance


def test_local_acceptance_checks_dashboard_and_authenticated_mcp_without_invoking_tools(monkeypatch):
    monkeypatch.setattr(
        local_acceptance,
        "guarded_loopback_http_get",
        lambda url, **kwargs: (200, json.dumps({
            "readiness": "ready",
            "checks": {"progress_journal": "ok", "delivery_supervisor": "ok"},
        }).encode()),
    )
    monkeypatch.setattr(
        local_acceptance,
        "probe_local_mcp",
        lambda **kwargs: {
            "status": "connected",
            "authenticated": True,
            "protocol_version": "2025-03-26",
            "server_info": {"name": "AUREN", "version": "1"},
            "tool_count": 8,
            "tool_names": sorted(local_acceptance._REQUIRED_TOOLS),
            "tools_invoked": False,
        },
    )

    report = local_acceptance.verify_local_experience(
        dashboard_url="http://127.0.0.1:8765",
        mcp_url="http://127.0.0.1:8766/mcp",
        token_file="/private/auren-token",
    )

    assert report["status"] == "ready"
    assert report["read_only"] is True
    assert report["checks"]["dashboard"]["status"] == "ready"
    assert report["checks"]["mcp"]["status"] == "ready"
    assert report["checks"]["mcp"]["tools_invoked"] is False


def test_local_acceptance_fails_closed_when_dashboard_or_required_tools_are_missing(monkeypatch):
    monkeypatch.setattr(
        local_acceptance,
        "guarded_loopback_http_get",
        lambda url, **kwargs: (200, b'{"readiness":"degraded","checks":{}}'),
    )
    monkeypatch.setattr(
        local_acceptance,
        "probe_local_mcp",
        lambda **kwargs: {
            "authenticated": True,
            "protocol_version": "2025-03-26",
            "server_info": {},
            "tool_count": 1,
            "tool_names": ["auren.service_status"],
        },
    )

    report = local_acceptance.verify_local_experience(
        dashboard_url="http://127.0.0.1:8765",
        mcp_url="http://127.0.0.1:8766/mcp",
        token_file="/private/auren-token",
    )

    assert report["status"] == "blocked"
    assert report["checks"]["dashboard"]["status"] == "blocked"
    assert report["checks"]["mcp"]["status"] == "blocked"
    assert "auren.submit_task" in report["checks"]["mcp"]["missing_required_tools"]


def test_local_acceptance_accepts_printed_console_url_and_rejects_unsafe_origins():
    assert local_acceptance._dashboard_health_url("http://127.0.0.1:8765/control-center") == "http://127.0.0.1:8765/api/health"
    for value in (
        "http://localhost:8765",
        "http://0.0.0.0:8765",
        "https://127.0.0.1:8765",
        "http://127.0.0.1:8765/not-the-console",
        "http://user@127.0.0.1:8765",
    ):
        try:
            local_acceptance._dashboard_health_url(value)
        except ValueError:
            continue
        assert False, f"expected unsafe dashboard origin to be rejected: {value}"
