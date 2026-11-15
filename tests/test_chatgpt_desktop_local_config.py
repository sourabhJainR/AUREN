from __future__ import annotations

import tomllib
from pathlib import Path


def test_chatgpt_desktop_uses_local_stdio_without_oauth():
    template = Path(__file__).resolve().parents[1] / "integrations" / "mcp" / "chatgpt-desktop.local.toml.example"
    config = tomllib.loads(template.read_text(encoding="utf-8"))
    server = config["mcp_servers"]["auren"]

    assert server["command"].endswith("python.exe")
    assert server["args"][1] == "mcp-proxy"
    assert server["args"][server["args"].index("--url") + 1] == "http://127.0.0.1:8765/mcp"
    assert server["args"][server["args"].index("--token-file") + 1].endswith(
        ".auren\\mcp\\local-operator.token"
    )
    assert "url" not in server
    assert "oauth" not in str(server).lower()
    assert "token" not in server.get("env", {})
