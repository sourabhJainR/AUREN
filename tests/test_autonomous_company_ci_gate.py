from pathlib import Path
from portable.autonomous_company import AutonomousCompany

def test_ci_wait_records_pass_after_pending(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("ci gate")
    states = iter([("pending", "running", ("ci-1",)), ("passed", "green", ("ci-2",))])
    assert company.wait_for_ci(unit.id, lambda: next(states), poll_seconds=0.01, timeout_seconds=1) == "passed"
    score = company.trust(unit.id)
    assert score.components["ci"] == 1.0

import pytest


def test_ci_wait_rejects_invalid_time_budgets(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("invalid ci budget")
    with pytest.raises(ValueError, match="poll_seconds"):
        company.wait_for_ci(unit.id, lambda: ("pending", "", ()), poll_seconds=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        company.wait_for_ci(unit.id, lambda: ("pending", "", ()), timeout_seconds=float("inf"))
    with pytest.raises(ValueError, match="max_polls"):
        company.wait_for_ci(unit.id, lambda: ("pending", "", ()), max_polls=0)


def test_ci_wait_poll_budget_terminates_pending_provider(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("bounded ci polls")
    calls = []
    result = company.wait_for_ci(
        unit.id,
        lambda: calls.append(1) or ("pending", "still running", ()),
        poll_seconds=0.001,
        timeout_seconds=2,
        max_polls=2,
    )
    assert result == "poll_limit"
    assert len(calls) == 2


def test_ci_wait_can_be_cancelled_responsively_during_sleep(tmp_path: Path, monkeypatch):
    import portable.autonomous_company as module

    company = AutonomousCompany(tmp_path)
    unit = company.start("cancel ci wait")
    stopped = {"value": False}
    calls = []
    def fake_sleep(_seconds):
        stopped["value"] = True

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    result = company.wait_for_ci(
        unit.id,
        lambda: calls.append(1) or ("pending", "still running", ()),
        poll_seconds=30,
        timeout_seconds=60,
        stop=lambda: stopped["value"],
    )
    assert result == "cancelled"
    assert len(calls) == 1
