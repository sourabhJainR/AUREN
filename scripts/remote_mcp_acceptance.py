"""Read-only live acceptance probe for a deployed AUREN remote MCP service."""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from portable.guarded_network import guarded_https_get

LEGACY_PROTOCOL_VERSION = "2025-11-25"
MODERN_PROTOCOL_VERSION = "2026-07-28"
REQUIRED_READ_TOOLS = frozenset({
    "auren.get_profile", "auren.service_status", "auren.list_tasks",
    "auren.get_task", "auren.get_evidence", "auren.list_approvals",
    "auren.get_approval",
})
REQUIRED_READ_SCOPES = {
    "auren.get_profile": "auren:tasks:read",
    "auren.service_status": "auren:tasks:read",
    "auren.list_tasks": "auren:tasks:read",
    "auren.get_task": "auren:tasks:read",
    "auren.get_evidence": "auren:evidence:read",
    "auren.list_approvals": "auren:approvals:read",
    "auren.get_approval": "auren:approvals:read",
}
REQUIRED_WRITE_SCOPES = {
    "auren.submit_task": "auren:tasks:write",
    "auren.decide_approval": "auren:approvals:decide",
}


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


def _https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https" and bool(parsed.hostname)
        and parsed.username is None and parsed.password is None
        and port in {None, 443} and not parsed.query and not parsed.fragment
    )


def _authorization_metadata_urls(issuer: str) -> tuple[str, ...]:
    parsed = urlsplit(issuer)
    origin = f"https://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    urls = (
        origin + "/.well-known/oauth-authorization-server" + path,
        issuer.rstrip("/") + "/.well-known/openid-configuration",
    )
    return tuple(dict.fromkeys(urls))


def _load_authorization_metadata(
    issuer: str,
    *,
    transport: Callable[..., tuple[int, bytes]],
    refresh_scope: str,
    allowed_hosts: tuple[str, ...],
) -> str:
    if not _https_url(issuer):
        raise AcceptanceFailure("authorization server issuer must be an exact HTTPS URL")
    host = (urlsplit(issuer).hostname or "").lower()
    if host not in {item.lower() for item in allowed_hosts}:
        raise AcceptanceFailure("authorization server host is not in the explicit outbound allowlist")
    metadata = None
    metadata_url = None
    for candidate in _authorization_metadata_urls(issuer):
        status, raw = transport(
            candidate, allowed_hosts=(host,), method="GET",
            headers={"accept": "application/json"}, timeout=10, max_bytes=64 * 1024,
        )
        if status == 404:
            continue
        metadata = _json_response(status, raw, "authorization-server metadata")
        metadata_url = candidate
        break
    if not isinstance(metadata, Mapping) or metadata_url is None:
        raise AcceptanceFailure("OAuth authorization-server discovery metadata was not found")
    if metadata.get("issuer") != issuer:
        raise AcceptanceFailure("OAuth discovery issuer does not exactly match protected-resource metadata")
    allowlist = {item.lower() for item in allowed_hosts}
    for key in ("authorization_endpoint", "token_endpoint"):
        endpoint_value = metadata.get(key)
        if not _https_url(endpoint_value):
            raise AcceptanceFailure(f"OAuth discovery {key} must be an HTTPS URL")
        if (urlsplit(endpoint_value).hostname or "").lower() not in allowlist:
            raise AcceptanceFailure(f"OAuth discovery {key} host is not in the explicit outbound allowlist")
    challenges = metadata.get("code_challenge_methods_supported")
    if not isinstance(challenges, list) or "S256" not in challenges:
        raise AcceptanceFailure("OAuth provider must advertise PKCE S256")
    auth_methods = metadata.get("token_endpoint_auth_methods_supported")
    if not isinstance(auth_methods, list) or not ({"none", "private_key_jwt"} & set(auth_methods)):
        raise AcceptanceFailure("OAuth provider must support none or private_key_jwt client authentication")
    registration_endpoint = metadata.get("registration_endpoint")
    if registration_endpoint is not None and (
        not _https_url(registration_endpoint)
        or (urlsplit(registration_endpoint).hostname or "").lower() not in allowlist
    ):
        raise AcceptanceFailure("OAuth registration endpoint must be HTTPS and explicitly allowlisted")
    if metadata.get("client_id_metadata_document_supported") is not True and not _https_url(registration_endpoint):
        raise AcceptanceFailure("OAuth provider must support CIMD or dynamic client registration")
    response_types = metadata.get("response_types_supported")
    if isinstance(response_types, list) and "code" not in response_types:
        raise AcceptanceFailure("OAuth provider does not advertise the authorization-code response")
    grant_types = metadata.get("grant_types_supported")
    if isinstance(grant_types, list) and "authorization_code" not in grant_types:
        raise AcceptanceFailure("OAuth provider does not advertise the authorization-code grant")
    scopes = metadata.get("scopes_supported")
    if not (
        isinstance(scopes, list) and refresh_scope in scopes
        or isinstance(grant_types, list) and "refresh_token" in grant_types
    ):
        raise AcceptanceFailure(f"OAuth provider must support {refresh_scope} or advertise refresh-token grants")
    return metadata_url


def _has_oauth_scope(tool: Mapping[str, Any], scope: str, *, top_level: bool = False) -> bool:
    meta = tool.get("_meta")
    source = tool.get("securitySchemes") if top_level else (meta.get("securitySchemes") if isinstance(meta, Mapping) else None)
    if not isinstance(source, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("type") == "oauth2"
        and isinstance(item.get("scopes"), list)
        and scope in item["scopes"]
        for item in source
    )


def run_acceptance(
    endpoint: str,
    token: str,
    *,
    allowed_hosts: tuple[str, ...],
    expected_issuer: str | None = None,
    refresh_scope: str = "offline_access",
    require_write: bool = False,
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
    health_status, health_raw = transport(
        origin + "/healthz", allowed_hosts=host_allowlist,
        method="GET", headers=headers, timeout=10, max_bytes=16 * 1024,
    )
    health = _json_response(health_status, health_raw, "health check")
    if health.get("status") != "ok":
        raise AcceptanceFailure("health check did not report status=ok")

    ready_status, ready_raw = transport(
        origin + "/readyz", allowed_hosts=host_allowlist,
        method="GET", headers=headers, timeout=10, max_bytes=16 * 1024,
    )
    readiness = _json_response(ready_status, ready_raw, "readiness check")
    if readiness.get("status") != "ok":
        raise AcceptanceFailure("readiness check did not report status=ok")

    metadata_url = origin + "/.well-known/oauth-protected-resource/mcp"
    metadata_status, metadata_raw = transport(
        metadata_url, allowed_hosts=host_allowlist,
        method="GET", headers=headers, timeout=10, max_bytes=64 * 1024,
    )
    metadata = _json_response(metadata_status, metadata_raw, "protected-resource metadata")
    if metadata.get("resource") != endpoint:
        raise AcceptanceFailure("protected-resource metadata does not match the configured endpoint")
    auth_servers = metadata.get("authorization_servers", [])
    if not isinstance(auth_servers, list) or len(auth_servers) != 1 or not isinstance(auth_servers[0], str):
        raise AcceptanceFailure("protected-resource metadata must identify exactly one OAuth issuer")
    issuer = auth_servers[0]
    if expected_issuer is not None and issuer != expected_issuer:
        raise AcceptanceFailure("protected-resource issuer does not exactly match the configured issuer")
    auth_metadata_url = _load_authorization_metadata(
        issuer, transport=transport, refresh_scope=refresh_scope, allowed_hosts=allowed_hosts,
    )

    def rpc(
        method: str, request_id: int, params: Mapping[str, Any] | None = None,
        *, protocol_version: str = LEGACY_PROTOCOL_VERSION, include_auth: bool = True,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        request_headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": protocol_version,
        }
        if include_auth:
            request_headers["authorization"] = "Bearer " + token
        if protocol_version == MODERN_PROTOCOL_VERSION:
            request_headers["mcp-method"] = method
            if isinstance(params, Mapping):
                if isinstance(params.get("name"), str):
                    request_headers["mcp-name"] = params["name"]
                elif isinstance(params.get("uri"), str):
                    request_headers["mcp-name"] = params["uri"]
        status, raw = transport(
            endpoint,
            allowed_hosts=host_allowlist,
            method="POST",
            headers=request_headers,
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

    modern_meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "auren-live-acceptance", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    discovery = rpc(
        "server/discover", 1, {"_meta": modern_meta},
        protocol_version=MODERN_PROTOCOL_VERSION,
    )
    supported = discovery.get("supportedVersions")
    if not isinstance(supported, list) or MODERN_PROTOCOL_VERSION not in supported:
        raise AcceptanceFailure("server/discover did not advertise the modern MCP protocol")
    if discovery.get("resultType") != "complete":
        raise AcceptanceFailure("server/discover did not return a complete modern result")
    server_info = discovery.get("_meta", {}).get("io.modelcontextprotocol/serverInfo", {})
    if not isinstance(server_info, Mapping) or server_info.get("name") != "auren-operator-mcp":
        raise AcceptanceFailure("unexpected modern MCP server identity")

    modern_tools = rpc(
        "tools/list", 2, {"_meta": modern_meta},
        protocol_version=MODERN_PROTOCOL_VERSION,
    )
    if modern_tools.get("resultType") != "complete" or modern_tools.get("cacheScope") != "private":
        raise AcceptanceFailure("modern tools/list is missing required result/cache metadata")
    tools = modern_tools.get("tools")
    if not isinstance(tools, list):
        raise AcceptanceFailure("modern tools/list did not return a tools array")
    names = {
        item.get("name") for item in tools
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    missing = sorted(REQUIRED_READ_TOOLS - names)
    if missing:
        raise AcceptanceFailure("required read-only tools are missing: " + ", ".join(missing))
    tool_by_name = {
        item["name"]: item for item in tools
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    missing_scopes = sorted(
        name for name, scope in REQUIRED_READ_SCOPES.items()
        if not _has_oauth_scope(tool_by_name[name], scope)
    )
    if missing_scopes:
        raise AcceptanceFailure("required read-only OAuth scopes are missing: " + ", ".join(missing_scopes))
    profile_tool = tool_by_name["auren.get_profile"]
    profile_meta = profile_tool.get("_meta", {})
    profile_schema = profile_tool.get("outputSchema", {})
    profile_properties = profile_schema.get("properties", {}) if isinstance(profile_schema, Mapping) else {}
    profile_id_schema = profile_properties.get("id", {}) if isinstance(profile_properties, Mapping) else {}
    if (
        not isinstance(profile_meta, Mapping)
        or profile_meta.get("openai/profile") is not True
        or not _has_oauth_scope(profile_tool, "auren:tasks:read", top_level=True)
        or not _has_oauth_scope(profile_tool, "auren:tasks:read")
        or not isinstance(profile_schema, Mapping)
        or profile_schema.get("type") != "object"
        or "id" not in profile_schema.get("required", [])
        or not isinstance(profile_id_schema, Mapping)
        or profile_id_schema.get("type") != "string"
        or profile_id_schema.get("minLength") != 1
        or profile_id_schema.get("pattern") != r"\S"
    ):
        raise AcceptanceFailure("get_profile is missing OpenAI profile metadata, schema or read-scope declarations")
    write_tools_verified = all(
        name in tool_by_name and _has_oauth_scope(tool_by_name[name], scope)
        for name, scope in REQUIRED_WRITE_SCOPES.items()
    )
    if require_write and not write_tools_verified:
        raise AcceptanceFailure("write tools or their required OAuth scopes are missing")

    initialized = rpc("initialize", 3, {
        "protocolVersion": LEGACY_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "auren-live-acceptance", "version": "1.0.0"},
    })
    if initialized.get("protocolVersion") != LEGACY_PROTOCOL_VERSION:
        raise AcceptanceFailure("server did not retain legacy MCP initialization support")
    legacy_info = initialized.get("serverInfo", {})
    if not isinstance(legacy_info, Mapping) or legacy_info.get("name") != "auren-operator-mcp":
        raise AcceptanceFailure("unexpected legacy MCP server identity")

    legacy_tools = rpc("tools/list", 4, protocol_version=LEGACY_PROTOCOL_VERSION)
    legacy_names = {
        item.get("name") for item in legacy_tools.get("tools", [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    missing = sorted(REQUIRED_READ_TOOLS - legacy_names)
    if missing:
        raise AcceptanceFailure("legacy required read-only tools are missing: " + ", ".join(missing))

    challenge = rpc(
        "tools/call", 5,
        {"name": "auren.get_profile", "arguments": {}},
        include_auth=False,
    )
    challenge_meta = challenge.get("_meta")
    challenges = challenge_meta.get("mcp/www_authenticate", []) if isinstance(challenge_meta, Mapping) else []
    challenge_text = " ".join(item for item in challenges if isinstance(item, str)) if isinstance(challenges, list) else ""
    if (
        challenge.get("isError") is not True
        or "resource_metadata=" not in challenge_text
        or metadata_url not in challenge_text
        or "error=" not in challenge_text
        or "error_description=" not in challenge_text
    ):
        raise AcceptanceFailure("unauthenticated profile call did not return a ChatGPT OAuth challenge")

    profile_result = rpc(
        "tools/call", 6, {"name": "auren.get_profile", "arguments": {}},
    )
    profile = profile_result.get("structuredContent")
    content = profile_result.get("content", [])
    if (
        not isinstance(profile, Mapping)
        or not isinstance(profile.get("id"), str)
        or not re.search(r"\S", profile["id"])
        or not isinstance(content, list) or not content
        or not isinstance(content[0], Mapping)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise AcceptanceFailure("authenticated get_profile returned an invalid structured profile")
    try:
        text_profile = json.loads(content[0]["text"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure("get_profile text content is not valid JSON") from exc
    if text_profile != profile:
        raise AcceptanceFailure("get_profile structuredContent and text content do not match")
    repeated_profile = rpc(
        "tools/call", 7, {"name": "auren.get_profile", "arguments": {}},
    )
    repeated = repeated_profile.get("structuredContent")
    if not isinstance(repeated, Mapping) or repeated.get("id") != profile["id"]:
        raise AcceptanceFailure("get_profile identity changed between calls")

    return {
        "status": "accepted",
        "endpoint": endpoint,
        "protocol_versions": [MODERN_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION],
        "preferred_protocol_version": MODERN_PROTOCOL_VERSION,
        "server_name": server_info.get("name"),
        "server_version": server_info.get("version"),
        "tool_count": len(names),
        "required_read_tools": sorted(REQUIRED_READ_TOOLS),
        "authorization_server": issuer,
        "authorization_metadata_url": auth_metadata_url,
        "oauth_discovery_verified": True,
        "profile_identity_verified": True,
        "tool_level_oauth_challenge_verified": True,
        "write_tools_verified": write_tools_verified,
        "live_task_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("AUREN_MCP_ENDPOINT", ""))
    parser.add_argument("--token-env", default="AUREN_MCP_ACCESS_TOKEN")
    parser.add_argument("--allowed-hosts", default=os.environ.get("AUREN_MCP_ALLOWED_HOSTS", ""))
    parser.add_argument("--expected-issuer", default=os.environ.get("AUREN_MCP_AUTHORIZATION_SERVER", ""))
    parser.add_argument("--refresh-scope", default=os.environ.get("AUREN_MCP_REFRESH_SCOPE", "offline_access"))
    parser.add_argument("--require-write", action="store_true", default=os.environ.get("AUREN_MCP_REQUIRE_WRITE") == "1")
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "")
    hosts = tuple(item.strip() for item in args.allowed_hosts.split(",") if item.strip())
    try:
        report = run_acceptance(
            args.endpoint, token, allowed_hosts=hosts,
            expected_issuer=args.expected_issuer or None,
            refresh_scope=args.refresh_scope,
            require_write=args.require_write,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:400]}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
