from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from portable.onboarding import (
    OnboardingError,
    configure_clients,
    initialize_repository,
    inspect_repository,
    doctor,
    discover_clients,
    load_project_profile,
)


def test_inspection_is_read_only_and_detects_existing_repo_state(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("keep me", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    report = inspect_repository(tmp_path)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert report["languages"] == ["python"]
    assert report["instruction_files"] == ["AGENTS.md"]
    assert report["inspection_policy"] == "read_only_no_repository_scripts_executed"


def test_init_creates_only_project_profile_and_preserves_existing_files(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("user-owned instructions", encoding="utf-8")
    result = initialize_repository(tmp_path)
    profile = tmp_path / ".auren" / "project.json"
    assert result["created"] is True
    assert profile.is_file()
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "user-owned instructions"
    assert json.loads(profile.read_text(encoding="utf-8"))["execution_authority"] == "host_managed_only"
    with pytest.raises(OnboardingError, match="refusing to overwrite"):
        initialize_repository(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated Windows privileges")
def test_profile_load_refuses_symlinked_state_directory(tmp_path: Path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "project.json").write_text(json.dumps({
        "schema_version": "auren-project-profile-v1",
        "runtime_mode": "single_user_local",
        "managed_runtime": {"enabled": False, "runtime_factory": None},
    }), encoding="utf-8")
    (project / ".auren").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OnboardingError, match="must not be a symlink"):
        load_project_profile(project)


def test_init_dry_run_does_not_create_repository_state(tmp_path: Path):
    result = initialize_repository(tmp_path, apply=False)
    assert result["created"] is False
    assert not (tmp_path / ".auren").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated Windows privileges")
def test_init_refuses_symlinked_auren_directory(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".auren").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OnboardingError, match="must not be a symlink"):
        initialize_repository(tmp_path)


def test_connect_preview_and_apply_preserve_other_mcp_entries(tmp_path: Path):
    config = tmp_path / "claude_desktop_config.json"
    original = {
        "theme": "dark",
        "mcpServers": {
            "existing": {"command": "existing-client", "args": ["keep"]},
            "auren": {"command": "old-auren"},
        },
    }
    config.write_text(json.dumps(original, indent=2), encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("x" * 64, encoding="utf-8")
    token.chmod(0o600)
    clients = [{"name": "Claude Desktop", "path": str(config), "key": "mcpServers"}]
    preview = configure_clients(
        mcp_url="http://127.0.0.1:8765/mcp", token_file=token,
        clients=clients, apply=False,
    )
    assert preview["clients"][0]["status"] == "preview"
    assert json.loads(config.read_text(encoding="utf-8")) == original
    applied = configure_clients(
        mcp_url="http://127.0.0.1:8765/mcp", token_file=token,
        clients=clients, apply=True,
    )
    result = json.loads(config.read_text(encoding="utf-8"))
    assert applied["clients"][0]["status"] == "configured"
    assert Path(applied["clients"][0]["backup"]).is_file()
    assert result["theme"] == "dark"
    assert result["mcpServers"]["existing"] == original["mcpServers"]["existing"]
    assert result["mcpServers"]["auren"]["args"][-1] == str(token)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated Windows privileges")
def test_connect_refuses_duplicate_keys_and_symlinked_token(tmp_path: Path):
    config = tmp_path / "client.json"
    config.write_text('{"mcpServers": {}, "mcpServers": {}}', encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("x" * 64, encoding="utf-8")
    clients = [{"name": "test", "path": str(config), "key": "mcpServers"}]
    with pytest.raises(OnboardingError, match="duplicate JSON key"):
        configure_clients(
            mcp_url="http://127.0.0.1:8765/mcp", token_file=token,
            clients=clients, apply=False,
        )
    config.write_text('{"mcpServers": {}}', encoding="utf-8")
    link = tmp_path / "token-link"
    link.symlink_to(token)
    with pytest.raises(OnboardingError, match="credential is not present"):
        configure_clients(
            mcp_url="http://127.0.0.1:8765/mcp", token_file=link,
            clients=clients, apply=False,
        )


def test_codex_toml_configuration_preserves_existing_settings_and_is_idempotent(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    original = '[mcp_servers.other]\ncommand = "other-client"\nargs = ["keep"]\n'
    config.write_text(original, encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("x" * 64, encoding="utf-8")
    token.chmod(0o600)
    clients = [{"name": "Codex CLI / app", "path": str(config),
                "key": "mcp_servers", "format": "toml"}]
    preview = configure_clients(mcp_url="http://127.0.0.1:8765/mcp",
                                token_file=token, clients=clients, apply=False)
    assert preview["clients"][0]["status"] == "preview"
    assert config.read_text(encoding="utf-8") == original
    applied = configure_clients(mcp_url="http://127.0.0.1:8765/mcp",
                                token_file=token, clients=clients, apply=True)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert applied["clients"][0]["status"] == "configured"
    assert Path(applied["clients"][0]["backup"]).read_text(encoding="utf-8") == original
    assert parsed["mcp_servers"]["other"]["args"] == ["keep"]
    assert parsed["mcp_servers"]["auren"]["args"][-2:] == ["--token-file", str(token)]
    repeated = configure_clients(mcp_url="http://127.0.0.1:8765/mcp",
                                 token_file=token, clients=clients, apply=True)
    assert repeated["clients"][0]["status"] == "already_configured"


def test_best_effort_client_configuration_isolates_bad_client_files(tmp_path: Path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"mcpServers": {}, "mcpServers": {}}', encoding="utf-8")
    healthy = tmp_path / "healthy.json"
    healthy.write_text('{"theme": "dark"}', encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("x" * 64, encoding="utf-8")
    token.chmod(0o600)
    result = configure_clients(
        mcp_url="http://127.0.0.1:8765/mcp", token_file=token, apply=True,
        best_effort=True,
        clients=[{"name": "Broken host", "path": str(broken), "key": "mcpServers"},
                 {"name": "Healthy host", "path": str(healthy), "key": "mcpServers"}],
    )
    assert [item["status"] for item in result["clients"]] == ["blocked", "configured"]
    assert result["blocked_count"] == 1
    assert json.loads(healthy.read_text(encoding="utf-8"))["mcpServers"]["auren"]["args"][-1] == str(token)


def test_codex_is_discovered_from_standard_user_config_path(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")
    codex = next(item for item in discover_clients(tmp_path)
                 if item["name"] == "Codex CLI / app")
    assert codex["path"] == str(config)
    assert codex["format"] == "toml"


def test_doctor_reports_local_and_multiple_remote_provider_readiness_without_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUREN_REMOTE_PROVIDER_ORDER", "groq,openai")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret-value")
    monkeypatch.setenv("GROQ_MODEL", "llama-current")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-value")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-current")
    monkeypatch.setenv("AUREN_LOCAL_REMOTE_INFERENCE", "enabled")
    monkeypatch.setenv("AUREN_LOCAL_DATA_CLASSIFICATION", "public")

    from portable.muxin_session_adapter import LocalModelDiscovery
    monkeypatch.setattr(
        LocalModelDiscovery, "discover",
        lambda self: {
            "available": True, "provider": "ollama-compatible",
            "models": [{"name": "qwen-coder", "size_bytes": 1024}],
            "latency_ms": 1.0, "resource_usage": {}, "fallback": "not_attempted",
        },
    )

    report = doctor(tmp_path)
    assert report["remote_model_providers"]["status"] == "configured_not_live_validated"
    assert report["remote_model_providers"]["provider_order"] == ["groq", "openai"]
    assert report["local_model"]["status"] == "detected_not_quality_validated"
    assert report["remote_inference_policy"] == {
        "enabled": True, "data_classification": "public",
    }
    serialized = json.dumps(report)
    assert "groq-secret-value" not in serialized
    assert "openai-secret-value" not in serialized
