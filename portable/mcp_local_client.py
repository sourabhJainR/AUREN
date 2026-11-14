"""Small authenticated MCP client for the local AUREN CLI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import mcp_stdio_proxy
from .remote_mcp import SUPPORTED_PROTOCOL_VERSIONS

LOCAL_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


class LocalMCPClientError(RuntimeError):
    """The local MCP endpoint could not complete a CLI operation."""


def _encode_rpc(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalMCPClientError("MCP request arguments must be finite JSON values") from exc


def _rpc_result(response: bytes | None, request_id: int) -> Mapping[str, Any]:
    if response is None:
        raise LocalMCPClientError("local MCP endpoint returned no response")
    try:
        raw = response.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LocalMCPClientError("local MCP endpoint returned invalid UTF-8") from exc
    try:
        decoded = json.loads(raw)
        messages = [decoded]
    except json.JSONDecodeError:
        messages = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LocalMCPClientError("local MCP endpoint returned invalid JSON-RPC") from exc
    for message in messages:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise LocalMCPClientError("local MCP endpoint returned an invalid JSON-RPC message")
        if message.get("id") != request_id:
            continue
        error = message.get("error")
        if isinstance(error, dict):
            detail = error.get("message")
            raise LocalMCPClientError(
                detail[:500] if isinstance(detail, str) else "local MCP request was rejected",
            )
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise LocalMCPClientError("local MCP endpoint returned no structured result")
        return result
    raise LocalMCPClientError("local MCP endpoint did not return the requested response")



def probe_local_mcp(
    *,
    url: str = "http://127.0.0.1:8765/mcp",
    token_file: str | Path,
    max_tools: int = 1000,
    max_pages: int = 20,
) -> Mapping[str, Any]:
    """Authenticate to the local AUREN MCP endpoint and list tools without invoking them."""
    if type(max_tools) is not int or not 1 <= max_tools <= 5000:
        raise ValueError("max_tools must be between 1 and 5000")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    endpoint = mcp_stdio_proxy.validate_local_url(url)
    token = mcp_stdio_proxy.read_token_file(Path(token_file).expanduser())

    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": LOCAL_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "AUREN integration probe", "version": "1.0"},
        },
    }
    initialized_result = _rpc_result(
        mcp_stdio_proxy.post_message(
            endpoint, token, _encode_rpc(initialize), timeout_seconds=15.0,
        ),
        1,
    )
    negotiated = initialized_result.get("protocolVersion")
    if negotiated != LOCAL_PROTOCOL_VERSION:
        raise LocalMCPClientError(
            "local MCP endpoint did not negotiate the protocol version required by the probe",
        )
    server_info = initialized_result.get("serverInfo")
    if not isinstance(server_info, Mapping):
        server_info = {}

    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    mcp_stdio_proxy.post_message(
        endpoint, token, _encode_rpc(notification),
        protocol_version=negotiated, timeout_seconds=15.0,
    )

    names: list[str] = []
    seen_names: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        pages += 1
        if pages > max_pages:
            raise LocalMCPClientError("local MCP tool listing exceeded the page limit")
        request_id = pages + 1
        params = {} if cursor is None else {"cursor": cursor}
        request = {
            "jsonrpc": "2.0", "id": request_id, "method": "tools/list",
            "params": params,
        }
        result = _rpc_result(
            mcp_stdio_proxy.post_message(
                endpoint, token, _encode_rpc(request),
                protocol_version=negotiated, timeout_seconds=30.0,
            ),
            request_id,
        )
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise LocalMCPClientError("local MCP tools/list response did not contain a tool list")
        for item in tools:
            if not isinstance(item, Mapping):
                raise LocalMCPClientError("local MCP tools/list response contained an invalid tool")
            name = item.get("name")
            if (
                not isinstance(name, str) or not name or len(name) > 128
                or not name.isprintable() or name in seen_names
            ):
                raise LocalMCPClientError("local MCP tools/list response contained an invalid or duplicate tool name")
            seen_names.add(name)
            names.append(name)
            if len(names) > max_tools:
                raise LocalMCPClientError("local MCP tool listing exceeded the tool limit")
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if (
            not isinstance(next_cursor, str) or not next_cursor
            or len(next_cursor) > 1024 or next_cursor in seen_cursors
        ):
            raise LocalMCPClientError("local MCP tools/list response contained an invalid pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    safe_server_info = {}
    for key in ("name", "version"):
        value = server_info.get(key)
        if isinstance(value, str) and value.isprintable() and len(value) <= 128:
            safe_server_info[key] = value
    return {
        "status": "connected",
        "endpoint": endpoint,
        "protocol_version": negotiated,
        "server_info": safe_server_info,
        "tool_count": len(names),
        "tool_names": names,
        "pages": pages,
        "authenticated": True,
        "tools_invoked": False,
        "remote_api_calls": False,
    }

def call_local_tool(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    url: str = "http://127.0.0.1:8765/mcp",
    token_file: str | Path,
) -> Mapping[str, Any]:
    """Call one local AUREN MCP tool with the private local operator credential."""
    if not isinstance(tool_name, str) or not tool_name.startswith("auren.") or len(tool_name) > 100:
        raise ValueError("tool_name must be a valid AUREN operator tool")
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    endpoint = mcp_stdio_proxy.validate_local_url(url)
    token = mcp_stdio_proxy.read_token_file(Path(token_file).expanduser())

    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": LOCAL_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "AUREN CLI", "version": "1.0"},
        },
    }
    response = mcp_stdio_proxy.post_message(
        endpoint, token, _encode_rpc(initialize),
        timeout_seconds=15.0,
    )
    negotiated = _rpc_result(response, 1).get("protocolVersion")
    if negotiated != LOCAL_PROTOCOL_VERSION:
        raise LocalMCPClientError(
            "local MCP endpoint did not negotiate the protocol version required by the CLI",
        )
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    mcp_stdio_proxy.post_message(
        endpoint, token, _encode_rpc(initialized),
        protocol_version=negotiated, timeout_seconds=15.0,
    )
    request = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": dict(arguments)},
    }
    result = _rpc_result(
        mcp_stdio_proxy.post_message(
            endpoint, token, _encode_rpc(request),
            protocol_version=negotiated, timeout_seconds=60.0,
        ),
        2,
    )
    if result.get("isError") is True:
        content = result.get("content")
        detail = next(
            (item.get("text") for item in content
             if isinstance(item, Mapping) and isinstance(item.get("text"), str)),
            "AUREN MCP tool returned an error",
        ) if isinstance(content, list) else "AUREN MCP tool returned an error"
        raise LocalMCPClientError(detail[:500])
    return result


__all__ = ["LOCAL_PROTOCOL_VERSION", "LocalMCPClientError", "call_local_tool", "probe_local_mcp"]
