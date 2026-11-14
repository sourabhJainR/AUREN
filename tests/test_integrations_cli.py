from __future__ import annotations

import json
from pathlib import Path

from auren_cli import main


def test_integrations_profile_init_and_validate(tmp_path: Path, capsys):
    assert main([
        "integrations", "profile", "init",
        "--project-root", str(tmp_path),
        "--provider", "ollama",
        "--model", "qwen2.5-coder:14b",
        "--apply",
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["applied"] is True
    assert created["role_count"] == 14

    assert main([
        "integrations", "profile", "validate",
        "--project-root", str(tmp_path),
    ]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["configuration_ready"] is True
    assert validated["route_policy_ready"] is True
    assert validated["live_activation"] == "not_performed"
    assert validated["private_data_routing"] == "local_only"


def test_integrations_inventory_command_is_read_only(tmp_path: Path, monkeypatch, capsys):
    from portable import integration_inventory

    captured = {}
    def fake_inventory(project_root, *, home=None, environ=None):
        captured.update({"project_root": str(project_root), "home": home})
        return {"schema_version": "auren-integration-inventory-v1", "mcp_hosts": []}

    monkeypatch.setattr(integration_inventory, "collect_integration_inventory", fake_inventory)
    assert main(["integrations", "--project-root", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "auren-integration-inventory-v1"
    assert captured["project_root"] == str(tmp_path)



def test_integrations_probe_local_mcp_is_read_only_and_uses_default_token_path(
    tmp_path: Path, monkeypatch, capsys,
):
    from portable import local_host, mcp_local_client

    token_path = tmp_path / "local-operator.token"
    captured = {}

    def fake_probe(*, url, token_file):
        captured["url"] = url
        captured["token_file"] = Path(token_file)
        return {
            "status": "connected",
            "protocol_version": "2026-07-28",
            "tool_count": 2,
            "tools_invoked": False,
        }

    monkeypatch.setattr(local_host, "local_token_path", lambda: token_path)
    monkeypatch.setattr(mcp_local_client, "probe_local_mcp", fake_probe)
    assert main(["integrations", "probe-local-mcp"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "connected"
    assert report["tools_invoked"] is False
    assert captured["url"] == "http://127.0.0.1:8765/mcp"
    assert captured["token_file"] == token_path


def test_integrations_probe_local_mcp_accepts_explicit_endpoint_and_token(
    tmp_path: Path, monkeypatch, capsys,
):
    from portable import local_host, mcp_local_client

    captured = {}

    def fake_probe(*, url, token_file):
        captured["url"] = url
        captured["token_file"] = Path(token_file)
        return {"status": "connected", "tools_invoked": False}

    monkeypatch.setattr(local_host, "local_token_path", lambda: tmp_path / "default-token")
    monkeypatch.setattr(mcp_local_client, "probe_local_mcp", fake_probe)
    explicit_token = tmp_path / "custom-token"
    assert main([
        "integrations", "probe-local-mcp",
        "--url", "http://127.0.0.1:9876/mcp",
        "--token-file", str(explicit_token),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "connected"
    assert captured["url"] == "http://127.0.0.1:9876/mcp"
    assert captured["token_file"] == explicit_token
