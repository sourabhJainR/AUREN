from __future__ import annotations

import json
from pathlib import Path

from auren_cli import _run_providers, _run_up


def test_up_initializes_only_missing_profile_and_starts_unified_host(tmp_path: Path, monkeypatch, capsys):
    from portable import local_host, onboarding

    monkeypatch.setattr(onboarding, "doctor", lambda root: {
        "providers": [], "remote_model_providers": {"status": "disabled"},
        "local_model": {"status": "not_available"},
        "remote_inference_policy": {"enabled": False},
    })
    captured = {}
    def fake_main(args):
        captured["args"] = args
        return 0
    monkeypatch.setattr(local_host, "main", fake_main)
    result = _run_up(["--project-root", str(tmp_path), "--no-browser"])
    assert result == 0
    profile = tmp_path / ".auren" / "project.json"
    assert profile.is_file()
    assert "auren up" not in capsys.readouterr().err
    args = captured["args"]
    assert "--auto-connect" in args
    assert "--open-browser" not in args
    assert str(tmp_path) in args


def test_up_preserves_existing_project_profile_and_supports_headless_opt_out(tmp_path: Path, monkeypatch):
    from portable.onboarding import initialize_repository
    from portable import local_host, onboarding

    monkeypatch.setattr(onboarding, "doctor", lambda root: {
        "providers": [], "remote_model_providers": {"status": "disabled"},
        "local_model": {"status": "not_available"},
        "remote_inference_policy": {"enabled": False},
    })
    initialize_repository(tmp_path)
    profile = tmp_path / ".auren" / "project.json"
    before = profile.read_bytes()
    captured = {}
    def fake_main(args):
        captured["args"] = args
        return 0
    monkeypatch.setattr(local_host, "main", fake_main)
    assert _run_up(["--project-root", str(tmp_path), "--no-connect", "--no-browser"]) == 0
    assert profile.read_bytes() == before
    assert "--auto-connect" not in captured["args"]
    assert "--open-browser" not in captured["args"]


def test_providers_command_reports_routes_without_credentials(monkeypatch, capsys, tmp_path: Path):
    from portable import onboarding

    monkeypatch.setattr(onboarding, "doctor", lambda root: {
        "providers": [{"id": "codex", "status": "detected_not_live_validated"}],
        "remote_model_providers": {"provider_order": ["openai"], "status": "configured_not_live_validated"},
        "local_model": {"available": True, "models": [{"name": "local-model"}]},
        "remote_inference_policy": {"enabled": True, "data_classification": "public"},
    })
    assert _run_providers(["--project-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["routing"]["provider_order"] == ["openai"]
    assert report["routing"]["private_and_restricted_tasks"] == "local_only"
    assert "API_KEY" not in output
