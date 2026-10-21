import json

import auren_cli


def test_continuous_cli_status_and_audit_are_read_only_json(tmp_path, capsys):
    assert auren_cli.main(["continuous", "status", "--project-root", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "stopped"
    assert status["task_counts"] == {}

    assert auren_cli.main(["continuous", "audit", "--project-root", str(tmp_path)]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit == {"audit_chain_valid": True}


def test_continuous_cli_stop_is_durable(tmp_path, capsys):
    assert auren_cli.main([
        "continuous", "stop", "--project-root", str(tmp_path), "--reason", "operator pause",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "stop_requested"
    assert result["stop_reason"] == "operator pause"
    assert auren_cli.main(["continuous", "audit", "--project-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["audit_chain_valid"] is True
