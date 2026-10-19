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
    assert output["idle_cycles"] == 2
