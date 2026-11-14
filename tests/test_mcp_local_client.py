from __future__ import annotations

import json

import pytest

from portable import mcp_stdio_proxy
from portable.mcp_local_client import (
    LOCAL_PROTOCOL_VERSION,
    LocalMCPClientError,
    call_local_tool,
    probe_local_mcp,
)


def test_local_cli_client_negotiates_stateless_mcp_and_calls_one_tool(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    token_file.chmod(0o600)
    calls = []

    def fake_post(url, token, payload, *, protocol_version=None, timeout_seconds=60.0):
        request = json.loads(payload.decode("utf-8"))
        calls.append((request, protocol_version, url, token))
        if request.get("method") == "initialize":
            response = {"jsonrpc": "2.0", "id": 1,
                        "result": {"protocolVersion": LOCAL_PROTOCOL_VERSION}}
            return json.dumps(response, indent=2).encode("utf-8")
        if request.get("method") == "notifications/initialized":
            return None
        response = {"jsonrpc": "2.0", "id": 2,
                    "result": {"content": [{"type": "text", "text": "accepted"}]}}
        return json.dumps(response).encode("utf-8")

    monkeypatch.setattr(mcp_stdio_proxy, "post_message", fake_post)
    result = call_local_tool(
        "auren.submit_task", {"task": "Build a feature"},
        url="http://127.0.0.1:8765/mcp", token_file=token_file,
    )
    assert result["content"][0]["text"] == "accepted"
    assert [item[0]["method"] for item in calls] == [
        "initialize", "notifications/initialized", "tools/call",
    ]
    assert calls[-1][0]["params"]["name"] == "auren.submit_task"
    assert calls[-1][1] == LOCAL_PROTOCOL_VERSION


def test_local_cli_client_rejects_non_loopback_endpoint_before_network(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    called = []
    monkeypatch.setattr(mcp_stdio_proxy, "post_message", lambda *a, **k: called.append(True))
    with pytest.raises(ValueError, match="loopback"):
        call_local_tool(
            "auren.get_task", {"task_id": "task-1"},
            url="https://example.com/mcp", token_file=token_file,
        )
    assert called == []


def test_local_cli_client_rejects_older_protocol_fallback(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    token_file.chmod(0o600)

    def fake_post(url, token, payload, *, protocol_version=None, timeout_seconds=60.0):
        return json.dumps({"jsonrpc": "2.0", "id": 1,
                           "result": {"protocolVersion": "2025-06-18"}}).encode("utf-8")

    monkeypatch.setattr(mcp_stdio_proxy, "post_message", fake_post)
    with pytest.raises(LocalMCPClientError, match="protocol version"):
        call_local_tool(
            "auren.get_task", {"task_id": "task-1"},
            url="http://127.0.0.1:8765/mcp", token_file=token_file,
        )



def test_local_mcp_probe_authenticates_and_lists_tools_without_invoking_them(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    token_file.chmod(0o600)
    calls = []

    def fake_post(url, token, payload, *, protocol_version=None, timeout_seconds=60.0):
        request = json.loads(payload.decode("utf-8"))
        calls.append((request, protocol_version, timeout_seconds))
        method = request.get("method")
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": LOCAL_PROTOCOL_VERSION,
                    "serverInfo": {"name": "AUREN", "version": "1.2.3"},
                },
            }
            return json.dumps(response).encode("utf-8")
        if method == "notifications/initialized":
            return None
        if method == "tools/list" and request["params"].get("cursor") is None:
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [{"name": "auren.get_task"}, {"name": "auren.list_evidence"}],
                    "nextCursor": "page-2",
                },
            }
            return json.dumps(response).encode("utf-8")
        if method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"tools": [{"name": "auren.get_approval"}]},
            }
            return json.dumps(response).encode("utf-8")
        raise AssertionError(f"probe must not invoke MCP tools: {method}")

    monkeypatch.setattr(mcp_stdio_proxy, "post_message", fake_post)
    report = probe_local_mcp(
        url="http://127.0.0.1:8765/mcp",
        token_file=token_file,
    )
    assert report["status"] == "connected"
    assert report["authenticated"] is True
    assert report["protocol_version"] == LOCAL_PROTOCOL_VERSION
    assert report["server_info"] == {"name": "AUREN", "version": "1.2.3"}
    assert report["tool_count"] == 3
    assert report["pages"] == 2
    assert report["tools_invoked"] is False
    assert report["remote_api_calls"] is False
    assert report["tool_names"] == [
        "auren.get_task", "auren.list_evidence", "auren.get_approval",
    ]
    assert [item[0]["method"] for item in calls] == [
        "initialize", "notifications/initialized", "tools/list", "tools/list",
    ]


def test_local_mcp_probe_rejects_invalid_tool_names_and_duplicate_cursors(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    token_file.chmod(0o600)

    page = {"count": 0}

    def fake_post(url, token, payload, *, protocol_version=None, timeout_seconds=60.0):
        request = json.loads(payload.decode("utf-8"))
        if request.get("method") == "initialize":
            return json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {"protocolVersion": LOCAL_PROTOCOL_VERSION},
            }).encode("utf-8")
        if request.get("method") == "notifications/initialized":
            return None
        page["count"] += 1
        name = "auren.ok" if page["count"] == 1 else "auren.ok-2"
        return json.dumps({
            "jsonrpc": "2.0", "id": request["id"],
            "result": {"tools": [{"name": name}], "nextCursor": "repeat"},
        }).encode("utf-8")

    monkeypatch.setattr(mcp_stdio_proxy, "post_message", fake_post)
    with pytest.raises(LocalMCPClientError, match="pagination cursor"):
        probe_local_mcp(token_file=token_file, max_pages=3)


def test_local_mcp_probe_rejects_non_loopback_before_reading_token_or_network(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        mcp_stdio_proxy,
        "post_message",
        lambda *args, **kwargs: called.append(True),
    )
    with pytest.raises(ValueError, match="loopback"):
        probe_local_mcp(
            url="https://example.com/mcp",
            token_file=tmp_path / "missing-token",
        )
    assert called == []



def test_local_mcp_probe_rejects_non_printable_tool_names(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 64, encoding="utf-8")
    token_file.chmod(0o600)

    def fake_post(url, token, payload, *, protocol_version=None, timeout_seconds=60.0):
        request = json.loads(payload.decode("utf-8"))
        if request.get("method") == "initialize":
            return json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {"protocolVersion": LOCAL_PROTOCOL_VERSION},
            }).encode("utf-8")
        if request.get("method") == "notifications/initialized":
            return None
        return json.dumps({
            "jsonrpc": "2.0", "id": request["id"],
            "result": {"tools": [{"name": "auren.\ninvalid"}]},
        }).encode("utf-8")

    monkeypatch.setattr(mcp_stdio_proxy, "post_message", fake_post)
    with pytest.raises(LocalMCPClientError, match="invalid or duplicate"):
        probe_local_mcp(token_file=token_file)
