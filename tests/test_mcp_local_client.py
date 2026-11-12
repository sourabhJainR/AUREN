from __future__ import annotations

import json

import pytest

from portable import mcp_stdio_proxy
from portable.mcp_local_client import LOCAL_PROTOCOL_VERSION, LocalMCPClientError, call_local_tool


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
    assert calls[-1][1] == MODERN_PROTOCOL_VERSION


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
