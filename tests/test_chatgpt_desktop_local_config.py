import tomllib
from pathlib import Path


def test_chatgpt_desktop_local_stdio_template_is_valid_and_has_no_oauth():
    template = (
        Path(__file__).resolve().parents[1]
        / "integrations"
        / "mcp"
        / "chatgpt-desktop.local.toml.example"
    )
    config_text = template.read_text(encoding="utf-8")
    config = tomllib.loads(config_text)
    server = config["mcp_servers"]["auren"]

    assert server["command"].endswith("/python.exe")
    assert server["args"] == [
        "C:/Path/To/AUREN/auren_cli.py",
        "mcp-proxy",
        "--url",
        "http://127.0.0.1:8765/mcp",
        "--token-file",
        "C:/Users/YourUser/.auren/mcp/local-operator.token",
    ]
    assert server["cwd"] == "C:/Path/To/AUREN"
    assert "url" not in server
    assert "oauth" not in server
    assert "bearer_token" not in server
    assert "authorization" not in server
    assert "https://" not in config_text.lower()
