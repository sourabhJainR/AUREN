"""Read-only live acceptance probe for a deployed AUREN remote MCP service."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from portable.guarded_network import guarded_https_get

PROTOCOL_VERSION = "2025-11-25"
REQUIRED_READ_TOOLS = frozenset({
    "auren.get_profile", "auren.service_status", "auren.list_tasks",
    "auren.get_task", "auren.get_evidence",
})


class AcceptanceFailure(RuntimeError):
    pass


def _json_response(status: int, raw: bytes, stage: str) -> Mapping[str, Any]:
    if status != 200:
        raise AcceptanceFailure(f"{stage} returned HTTP {status}")
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        raise AcceptanceFailure(f"{stage} response exceeded the size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure(f"{stage} returned invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise AcceptanceFailure(f"{stage} returned a non-object response")
    return value


def run_acceptance(
    endpoint: str,
    token: str,
    *,
    allowed_hosts: tuple[str, ...],
    transport: Callable[..., tuple[int, bytes]] = guarded_https_get,
) -> dict[str, Any]:
    """Verify TLS endpoint, health, OAuth resource metadata and read-only MCP discovery.

    This probe does not submit tasks, approve work, pause admissions, or mutate state.
    """
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    normalized_hosts = tuple(sorted({item.strip().lower() for item in allowed_hosts if item.strip()}))
    if (
        parsed.scheme != "https" or not host or parsed.username or parsed.password
        or parsed.port not in {None, 443} or parsed.query or parsed.fragment
        or parsed.path != "/mcp" or host not in normalized_hosts
    ):
        raise ValueError("endpoint must be an allowlisted HTTPS /mcp URL on port 443")
    if not isinstance(token, str) or len(token) < 16 or len(token) > 8192 or any(ch in token for ch in "\r\n"):
        raise ValueError("a valid deployment access token is required")

    origin = f"https://{host}"
    host_allowlist = (host,)
    headers = {
        "accept": "application/json",
        "authorization": "Bearer " + token,
    }
    _, health_raw = transport(
        origin + "/healthz", allowed_hosts=host_allowlist,
        method="GET", headers=headers, timeout=10, max_bytes=16 * 1024,
    )
    health = _json_response(200, health_raw, "health check")
    if health.get("status") != "ok":
        raise AcceptanceFailure("health check did not report status=ok")

    metadata_url = origin + "/.well-known/oauth-protected-resource/mcp"
    _, metadata_raw = transport(
        metadata_url, allowed_hosts=host_allowlist,
        method="GET", headers=headers, timeout=10, max_bytes=64 * 1024,
    )
    metadata = _json_response(200, metadata_raw, "protected-resource metadata")
    if metadata.get("resource") != endpoint:
        raise AcceptanceFailure("protected-resource metadata does not match the configured endpoint")
    auth_servers = metadata.get("authorization_servers", [])
    if not isinstance(auth_servers, list):
        raise AcceptanceFailure("authorization_servers metadata must be an array")

    def rpc(method: str, request_id: int, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        status, raw = transport(
            endpoint,
            allowed_hosts=host_allowlist,
            method="POST",
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "authorization": "Bearer " + token,
                "mcp-protocol-version": PROTOCOL_VERSION,
            },
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            timeout=15,
            max_bytes=2 * 1024 * 1024,
        )
        response = _json_response(status, raw, method)
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise AcceptanceFailure(f"{method} returned an invalid JSON-RPC envelope")
        if "error" in response:
            error = response["error"]
            message = error.get("message", "MCP request failed") if isinstance(error, Mapping) else "MCP request failed"
            raise AcceptanceFailure(f"{method} failed: {str(message)[:240]}")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise AcceptanceFailure(f"{method} returned no result object")
        return result

    initialized = rpc("initialize", 1, {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "auren-live-acceptance", "version": "1.0.0"},
    })
    if initialized.get("protocolVersion") != PROTOCOL_VERSION:
        raise AcceptanceFailure("server negotiated an unexpected MCP protocol version")
    server_info = initialized.get("serverInfo", {})
    if not isinstance(server_info, Mapping) or server_info.get("name") != "auren-operator-mcp":
        raise AcceptanceFailure("unexpected MCP server identity")

    listed = rpc("tools/list", 2)
    tools = listed.get("tools")
    if not isinstance(tools, list):
        raise AcceptanceFailure("tools/list did not return a tools array")
    names = {
        item.get("name") for item in tools
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    missing = sorted(REQUIRED_READ_TOOLS - names)
    if missing:
        raise AcceptanceFailure("required read-only tools are missing: " + ", ".join(missing))

    return {
        "status": "accepted",
        "endpoint": endpoint,
        "protocol_version": PROTOCOL_VERSION,
        "server_name": server_info.get("name"),
        "server_version": server_info.get("version"),
        "tool_count": len(names),
        "required_read_tools": sorted(REQUIRED_READ_TOOLS),
        "authorization_server_count": len(auth_servers),
        "live_task_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("AUREN_MCP_ENDPOINT", ""))
    parser.add_argument("--token-env", default="AUREN_MCP_ACCESS_TOKEN")
    parser.add_argument("--allowed-hosts", default=os.environ.get("AUREN_MCP_ALLOWED_HOSTS", ""))
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    hosts = tuple(item.strip() for item in args.allowed_hosts.split(",") if item.strip())
    try:
        report = run_acceptance(args.endpoint, token, allowed_hosts=hosts)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:400]}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
