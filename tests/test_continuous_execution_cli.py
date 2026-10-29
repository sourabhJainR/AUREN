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



def test_continuous_cli_wires_recovery_ledger_for_ci_repair(monkeypatch, tmp_path, capsys):
    from portable import continuous_execution, engineering_recovery

    captured = {}

    class FakeLedger:
        def __init__(self, path, **kwargs):
            captured["ledger_path"] = path
            captured["ledger_options"] = kwargs

    class FakeSupervisor:
        def __init__(self, controller, **kwargs):
            captured["supervisor"] = kwargs

        def run(self, **kwargs):
            captured["run_options"] = kwargs
            return {"state": "stopped", "audit_chain_valid": True}

    monkeypatch.setattr(engineering_recovery, "EngineeringRecoveryLedger", FakeLedger)
    monkeypatch.setattr(continuous_execution, "ContinuousExecutionSupervisor", FakeSupervisor)
    monkeypatch.setattr(
        continuous_execution, "GitHubActionsCheckPoller",
        lambda repository, head_sha, required_checks: (repository, head_sha, tuple(required_checks)),
    )
    assert auren_cli.main([
        "continuous", "start", "--project-root", str(tmp_path), "--max-cycles", "1",
        "--watch", "owner/repo@" + "a" * 40,
        "--required-check", "build", "--repair-handler", "tests.handlers.repair",
    ]) == 0
    assert captured["ledger_path"] == tmp_path / ".auren" / "engineering-recovery.sqlite3"
    assert captured["ledger_options"] == {
        "max_same_hypothesis_attempts": 2, "max_total_attempts": 12,
        "max_elapsed_seconds": 3600,
    }
    assert captured["supervisor"]["recovery_ledger"].__class__ is FakeLedger
    assert captured["supervisor"]["repair_handler"] == "tests.handlers.repair"
    assert captured["run_options"]["max_cycles"] == 1
    assert json.loads(capsys.readouterr().out)["audit_chain_valid"] is True


def _continuous_status_handler(_payload):
    return {"success": True, "evidence": ["continuous-cli-status-test"]}


def test_continuous_cli_status_projects_canonical_task_rows_after_restart(tmp_path, capsys):
    import gc

    from portable.execution_controller import ExecutionController

    controller = ExecutionController(tmp_path)
    handler_name = "tests.test_continuous_execution_cli._continuous_status_handler"
    controller.register_handler(
        handler_name, _continuous_status_handler, execution_class="read_only"
    )
    pending = controller.submit(
        handler_name, {"case": "pending"},
        goal="CLI status reads durable task rows",
        work_key="AUREN:test:continuous-cli-status-pending",
    )
    completed = controller.submit(
        handler_name, {"case": "completed"},
        goal="CLI status reads durable task rows",
        work_key="AUREN:test:continuous-cli-status-completed",
    )
    controller.run_once(completed.id)
    assert controller.task(pending.id).state == "pending"
    assert controller.task(completed.id).state == "completed"
    del controller
    gc.collect()

    assert auren_cli.main([
        "continuous", "status", "--project-root", str(tmp_path),
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "stopped"
    assert status["task_counts"] == {"completed": 1, "pending": 1}
    assert [row["task_id"] for row in status["pending_tasks"]] == [pending.id]
    assert status["pending_tasks"][0]["state"] == "pending"
    assert [row["task_id"] for row in status["recent_terminal_tasks"]] == [completed.id]
    assert status["recent_terminal_tasks"][0]["state"] == "completed"
