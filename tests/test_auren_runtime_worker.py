import json
from pathlib import Path

from portable.auren_runtime import main


def test_worker_cli_runs_a_bounded_resumable_slice(tmp_path: Path, capsys):
    code = main([
        "worker",
        "--project-root", str(tmp_path),
        "--idle-sleep-seconds", "0",
        "--max-cycles", "2",
        "--max-idle-cycles", "2",
        "--json",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["stop_reason"] == "idle_budget_exhausted"
    assert output["cycles"] == 2
    assert output["processed_work_units"] == 0
    assert output["completed_tasks"] == 0
    assert output["failed_tasks"] == 0
    assert output["remaining_nonterminal_tasks"] == 0
    assert output["idle_cycles"] == 2


def test_worker_cli_binds_stable_work_key_to_durable_controller_lifecycle(tmp_path: Path, capsys):
    args = [
        "worker",
        "--project-root", str(tmp_path),
        "--work-key", "AUREN:issue-696:continuous-engineering",
        "--goal", "Resume bounded AUREN continuous engineering",
        "--idle-sleep-seconds", "0",
        "--max-cycles", "2",
        "--max-idle-cycles", "2",
        "--json",
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["stable_work_key"] == "AUREN:issue-696:continuous-engineering"
    assert first["stable_work_unit_id"]

    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["stable_work_unit_id"] == first["stable_work_unit_id"]


def test_worker_cli_rejects_incomplete_stable_work_identity(tmp_path: Path):
    import pytest

    with pytest.raises(SystemExit, match="must be supplied together"):
        main(["worker", "--project-root", str(tmp_path), "--work-key", "AUREN:issue-696:continuous-engineering"])


def test_worker_key_recovers_unit_created_before_session_mapping(tmp_path: Path):
    from portable.execution_controller import ExecutionController

    controller = ExecutionController(tmp_path)
    key = "AUREN:issue-696:continuous-engineering"
    goal = "Resume bounded AUREN continuous engineering"
    # Simulate a process death after the company ledger committed but before
    # the controller's session-to-work-unit mapping was written.
    created = controller.company.start(goal, work_key=key)
    recovered = controller.work_unit_for_session(key, goal, work_key=key)
    assert recovered == created.id
    assert controller.work_unit_for_session(key, goal, work_key=key) == created.id


def test_worker_key_rejects_conflicting_goal(tmp_path: Path):
    import pytest
    from portable.execution_controller import ExecutionController

    controller = ExecutionController(tmp_path)
    key = "AUREN:issue-696:continuous-engineering"
    controller.work_unit_for_session(key, "Resume bounded AUREN continuous engineering", work_key=key)
    with pytest.raises(ValueError, match="different active goal"):
        controller.work_unit_for_session(key, "Unrelated goal", work_key=key)
