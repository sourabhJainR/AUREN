import json
from pathlib import Path

from auren_cli import _run_guard_policy, _run_workflow_profile


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


def test_guard_policy_cli_validate_show_and_dry_run(tmp_path: Path, capsys):
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(json.dumps({
        "schema_version": "auren-guard-v1",
        "default_effect": "allow",
        "rules": [{"id": "deny-shell", "effect": "deny", "tool": "trusted.shell"}],
    }), encoding="utf-8")

    assert _run_guard_policy(["--project-root", str(tmp_path), "validate"]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert validated["rule_ids"] == ["deny-shell"]
    assert len(validated["policy_digest"]) == 64

    assert _run_guard_policy(["--project-root", str(tmp_path), "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["configured"] is True
    assert shown["policy"]["rules"][0]["id"] == "deny-shell"

    assert _run_guard_policy([
        "--project-root", str(tmp_path), "dry-run", "--tool", "trusted.shell",
    ]) == 3
    simulated = json.loads(capsys.readouterr().out)
    assert simulated["decision"]["effect"] == "deny"
    assert simulated["decision"]["allowed"] is False
    assert "does not authorize" in simulated["note"]


def test_guard_policy_cli_rejects_malformed_policy(tmp_path: Path, capsys):
    policy_path = tmp_path / ".auren" / "guard_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        '{"schema_version":"auren-guard-v1","schema_version":"auren-guard-v1","rules":[]}',
        encoding="utf-8",
    )
    assert _run_guard_policy(["--project-root", str(tmp_path), "validate"]) == 2
    assert "Guard policy error" in capsys.readouterr().err
