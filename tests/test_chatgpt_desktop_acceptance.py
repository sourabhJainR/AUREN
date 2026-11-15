from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts.accept_chatgpt_desktop_mcp import AcceptanceError, run_acceptance


def _fixture(tmp_path: Path, url: str = "http://127.0.0.1:8765/mcp"):
    token = tmp_path / "operator.token"
    token.write_text("x" * 64, encoding="utf-8")
    fake = tmp_path / "fake_stdio.py"
    fake.write_text(
        """
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25"}
    elif method == "tools/list":
        result = {"tools": [{
            "name": "auren.service_status",
            "annotations": {"readOnlyHint": True},
        }]}
    elif method == "tools/call":
        assert request["params"]["name"] == "auren.service_status"
        result = {"content": [{"type": "text", "text": "{\\\\"status\\\\":\\\\"ready\\\\"}"}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        "[mcp_servers.auren]\n"
        f'command = "{sys.executable.replace(chr(92), "/")}"\n'
        f'args = ["{fake.as_posix()}", "mcp-proxy", "--url", "{url}", '
        f'"--token-file", "{token.as_posix()}"]\n'
        f'cwd = "{tmp_path.as_posix()}"\n',
        encoding="utf-8",
    )
    return config


def test_chatgpt_desktop_acceptance_runs_configured_stdio_process_and_read_only_call(tmp_path):
    config = _fixture(tmp_path)
    report = run_acceptance(config)

    assert report["status"] == "stdio_acceptance_passed"
    assert report["protocol_version"] == "2025-11-25"
    assert report["read_only_tool"] == "auren.service_status"
    assert report["read_only_call"] == "passed"
    assert report["task_submitted"] is False
    assert report["approval_changed"] is False
    assert report["desktop_ui_verified"] is False


def test_chatgpt_desktop_acceptance_rejects_non_loopback_before_launch(tmp_path):
    config = _fixture(tmp_path, "https://example.com/mcp")
    with pytest.raises(AcceptanceError, match="loopback"):
        run_acceptance(config)
