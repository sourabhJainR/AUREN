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
