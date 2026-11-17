import auren_cli


def test_export_command_dispatches_to_portable_project_export(monkeypatch):
    captured = {}
    monkeypatch.setattr(auren_cli, "_run_export", lambda args: captured.setdefault("args", args) and 0)
    assert auren_cli.main(["export", "--project-root", ".", "--output", "out.zip"]) == 0
    assert captured["args"] == ["--project-root", ".", "--output", "out.zip"]
