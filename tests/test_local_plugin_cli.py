from __future__ import annotations

import auren_cli


def test_plugin_install_routes_to_local_runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(auren_cli, "_run_up", lambda args: calls.append(args) or 0)
    assert auren_cli.main(["plugin", "install", "--project-root", "."]) == 0
    assert calls == [["--project-root", "."]]


def test_plugin_status_routes_to_doctor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auren_cli, "_run_onboarding",
        lambda command, args: calls.append((command, args)) or 0,
    )
    assert auren_cli.main(["plugin", "status", "--project-root", "."]) == 0
    assert calls == [("doctor", ["--project-root", "."])]


def test_plugin_connect_routes_to_existing_safe_client_configurator(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auren_cli, "_run_onboarding",
        lambda command, args: calls.append((command, args)) or 0,
    )
    assert auren_cli.main(["plugin", "connect", "--apply"]) == 0
    assert calls == [("connect", ["--apply"])]


def test_plugin_help_is_available_without_loading_runtime(capsys):
    assert auren_cli.main(["plugin", "--help"]) == 0
    output = capsys.readouterr().out
    assert "install" in output
    assert "status" in output
    assert "connect" in output


def test_unknown_plugin_action_fails_closed(capsys):
    assert auren_cli.main(["plugin", "enable-everything"]) == 2
    assert "unknown AUREN plugin action" in capsys.readouterr().err
