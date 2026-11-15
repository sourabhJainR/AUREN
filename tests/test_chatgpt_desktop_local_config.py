from pathlib import Path


def test_chatgpt_desktop_uses_local_stdio_without_oauth():
    template = Path(__file__).resolve().parents[1] / "integrations" / "mcp" / "chatgpt-desktop.local.toml.example"
    config = template.read_text(encoding="utf-8")

    assert '[mcp_servers.auren]' in config
    assert 'command = "C:\\\\Python\\\\python.exe"' in config
    assert '"mcp-proxy"' in config
    assert '"http://127.0.0.1:8765/mcp"' in config
    assert '"--token-file"' in config
    assert ".auren\\\\mcp\\\\local-operator.token" in config
    assert "oauth =" not in config.lower()
    assert "[mcp_servers.auren.oauth]" not in config.lower()
    assert "https://" not in config
