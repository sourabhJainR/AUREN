from pathlib import Path
from portable.autonomous_company import AutonomousCompany

def test_ci_wait_records_pass_after_pending(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("ci gate")
    states = iter([("pending", "running", ("ci-1",)), ("passed", "green", ("ci-2",))])
    assert company.wait_for_ci(unit.id, lambda: next(states), poll_seconds=0.01, timeout_seconds=1) == "passed"
    score = company.trust(unit.id)
    assert score.components["ci"] == 1.0
