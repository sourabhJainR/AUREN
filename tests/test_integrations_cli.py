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
    assert validated["activation_ready"] is True
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
