import json
from pathlib import Path

from auren_cli import _run_workflow_profile


def test_cli_profile_show_set_and_reset(tmp_path: Path, capsys):
    assert _run_workflow_profile(["--project-root", str(tmp_path), "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["source"] == "safe_default"
    assert shown["profile"]["delivery_mode"] == "pull_request"

    assert _run_workflow_profile([
        "--project-root", str(tmp_path), "set",
        "--delivery-mode", "stage_only",
        "--execution-mode", "autonomous",
        "--review-mode", "security_focused",
    ]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["source"] == "saved"
    assert configured["profile"]["delivery_mode"] == "stage_only"
    assert configured["profile"]["execution_mode"] == "autonomous"
    assert configured["profile"]["review_mode"] == "security_focused"
    assert len(configured["profile_digest"]) == 64

    profile_path = tmp_path / ".auren" / "workflow_profile.json"
    assert profile_path.is_file()
    assert _run_workflow_profile(["--project-root", str(tmp_path), "reset"]) == 0
    reset = json.loads(capsys.readouterr().out)
    assert reset["source"] == "safe_default"
    assert not profile_path.exists()


def test_cli_profile_show_fails_closed_on_corrupt_saved_profile(tmp_path: Path, capsys):
    profile_path = tmp_path / ".auren" / "workflow_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text('{"delivery_mode":"stage_only","delivery_mode":"pull_request"}', encoding="utf-8")
    assert _run_workflow_profile(["--project-root", str(tmp_path), "show"]) == 2
    assert "workflow profile error" in capsys.readouterr().err
