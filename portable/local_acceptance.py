"""Evidence-producing acceptance checks for AUREN's single-user local experience.

The check is deliberately read-only: it verifies dashboard readiness and an
authenticated MCP initialize/tools-list exchange, but never invokes a tool.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from .guarded_network import guarded_loopback_http_get
from .mcp_local_client import probe_local_mcp

_REQUIRED_TOOLS = frozenset({
    "auren.service_status",
    "auren.get_profile",
    "auren.list_tasks",
    "auren.get_task",
    "auren.submit_task",
    "auren.cancel_task",
    "auren.list_approvals",
    "auren.get_evidence",
})


def _dashboard_health_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("dashboard URL is required")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.port is None or not 1 <= parsed.port <= 65535
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/", "/control-center"} or parsed.query or parsed.fragment
    ):
        raise ValueError("dashboard URL must be an explicit http://127.0.0.1:<port> origin")
    return f"http://127.0.0.1:{parsed.port}/api/health"


def verify_local_experience(
    *,
    dashboard_url: str,
    mcp_url: str,
    token_file: str,
) -> dict[str, Any]:
    """Verify local console readiness and authenticated MCP discovery."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "blocked",
        "read_only": True,
        "checks": {},
    }
    try:
        health_url = _dashboard_health_url(dashboard_url)
        status, body = guarded_loopback_http_get(
            health_url, timeout=3.0, max_bytes=64 * 1024,
        )
        health = json.loads(body.decode("utf-8"))
        ready = status == 200 and isinstance(health, dict) and health.get("readiness") == "ready"
        report["checks"]["dashboard"] = {
            "status": "ready" if ready else "blocked",
            "http_status": status,
            "readiness": health.get("readiness") if isinstance(health, dict) else None,
            "health_checks": health.get("checks", {}) if isinstance(health, dict) else {},
        }
    except Exception as exc:
        report["checks"]["dashboard"] = {
            "status": "blocked",
            "reason": f"dashboard probe failed ({type(exc).__name__})",
        }

    try:
        mcp = probe_local_mcp(url=mcp_url, token_file=token_file)
        discovered = set(mcp.get("tool_names", []))
        missing = sorted(_REQUIRED_TOOLS - discovered)
        report["checks"]["mcp"] = {
            "status": "ready" if not missing and mcp.get("authenticated") is True else "blocked",
            "authenticated": mcp.get("authenticated") is True,
            "protocol_version": mcp.get("protocol_version"),
            "server_info": mcp.get("server_info", {}),
            "tool_count": mcp.get("tool_count", 0),
            "missing_required_tools": missing,
            "tools_invoked": False,
        }
    except Exception as exc:
        report["checks"]["mcp"] = {
            "status": "blocked",
            "reason": f"authenticated MCP probe failed ({type(exc).__name__})",
            "tools_invoked": False,
        }

    report["status"] = (
        "ready"
        if all(item.get("status") == "ready" for item in report["checks"].values())
        else "blocked"
    )
    return report


def render_report(report: dict[str, Any]) -> str:
    """Return stable, secret-free JSON suitable for CI artifacts."""
    return json.dumps(report, indent=2, sort_keys=True) + "\n"
