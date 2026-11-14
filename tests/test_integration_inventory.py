from __future__ import annotations

import json
from pathlib import Path

from portable.integration_inventory import collect_integration_inventory


def test_inventory_separates_detection_configuration_and_live_acceptance(tmp_path: Path, monkeypatch):
    from portable import onboarding

    home = tmp_path / "home"
    home.mkdir()
    claude_config = home / ".claude.json"
    claude_config.write_text(json.dumps({
        "mcpServers": {"auren": {"command": "python", "args": ["auren_cli.py", "mcp-proxy"]}},
    }), encoding="utf-8")
    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text('[mcp_servers.auren]\ncommand = "auren"\nargs = ["mcp-proxy"]\n', encoding="utf-8")
    manifests = home / ".auren" / "providers"
    manifests.mkdir(parents=True)
    (manifests / "custom-agent.json").write_text('{"capabilities":["research"]}', encoding="utf-8")
    skill = home / ".claude" / "skills" / "ai-coding-orchestrator" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# AUREN skill", encoding="utf-8")

    monkeypatch.setattr(onboarding, "discover_clients", lambda _home: [
        {"name": "Claude Code", "path": str(claude_config), "key": "mcpServers"},
        {"name": "Codex CLI", "path": str(codex_config), "key": "mcp_servers", "format": "toml"},
    ])
    monkeypatch.setattr(onboarding, "doctor", lambda _root: {
        "providers": [{"id": "codex", "command": "/usr/bin/codex", "version": "1.2", "capabilities": ["coding"]}],
        "local_model": {"available": True, "models": [{"name": "qwen2.5-coder:14b"}], "status": "detected_not_quality_validated"},
        "vibe_coding_host": {"status": "not_configured"},
    })
    env = {
        "OPENAI_API_KEY": "do-not-print-this-secret",
        "OPENAI_MODEL": "gpt-5.1",
        "AUREN_REMOTE_PROVIDER_ORDER": "openai",
        "AUREN_LOCAL_REMOTE_INFERENCE": "enabled",
        "AUREN_LOCAL_DATA_CLASSIFICATION": "public",
    }

    report = collect_integration_inventory(tmp_path, home=home, environ=env)
    encoded = json.dumps(report, sort_keys=True)

    assert report["schema_version"] == "auren-integration-inventory-v1"
    assert len(report["mcp_hosts"]) == 2
    assert all(host["configured"] for host in report["mcp_hosts"])
    assert all(host["authenticated"] == "not_tested" for host in report["mcp_hosts"])
    assert all(host["live_tested"] == "not_tested" for host in report["mcp_hosts"])
    assert report["remote_model_providers"]["providers"][0]["enabled"] is True
    assert report["local_model_provider"]["model_ids"] == ["qwen2.5-coder:14b"]
    assert report["provider_capability_manifests"][0]["trust"] == "untrusted_metadata_only"
    assert report["agent_skills"][0]["id"] == "ai-coding-orchestrator"
    assert "do-not-print-this-secret" not in encoded
    assert "OPENAI_API_KEY" not in encoded


def test_inventory_marks_a_host_detected_but_not_configured(tmp_path: Path, monkeypatch):
    from portable import onboarding

    home = tmp_path / "home"
    home.mkdir()
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir()
    config.write_text('{"mcpServers":{"other":{"command":"other"}}}', encoding="utf-8")
    monkeypatch.setattr(onboarding, "discover_clients", lambda _home: [
        {"name": "Cursor", "path": str(config), "key": "mcpServers"},
    ])
    monkeypatch.setattr(onboarding, "doctor", lambda _root: {
        "providers": [], "local_model": {"available": False, "models": []},
        "vibe_coding_host": {"status": "not_configured"},
    })
    report = collect_integration_inventory(tmp_path, home=home, environ={})
    assert report["mcp_hosts"][0]["detected"] is True
    assert report["mcp_hosts"][0]["configured"] is False
    assert report["mcp_hosts"][0]["status"] == "detected_not_configured"
