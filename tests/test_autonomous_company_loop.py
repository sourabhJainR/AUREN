import tempfile
from pathlib import Path

from portable.autonomous_company import AutonomousCompany


def test_iterate_yields_after_bounded_slice_and_resumes_from_durable_iteration():
    with tempfile.TemporaryDirectory() as tmp:
        company = AutonomousCompany(Path(tmp))
        unit = company.start("complete bounded engineering work", max_iterations=10)
        executions = []

        def execute(iteration):
            executions.append(iteration)
            return "passed", "implementation available", (f"execution:{iteration}",)

        def review(iteration):
            return (iteration >= 3, "review accepted" if iteration >= 3 else "same review defect", (f"review:{iteration}",))

        def recover(problem):
            return True, "alternate plan accepted for next bounded attempt", ("recovery:alternate",)

        first = company.iterate(
            unit.id, execute=execute, review=review, recover=recover,
            max_steps=2, max_consecutive_failures=3,
        )
        assert company._state(unit.id) == ("running", 2)
        assert executions == [1, 2]
        assert any(
            event["kind"] == "supervisor" and event["status"] == "yielded"
            for event in _events(company, unit.id)
        )

        second = company.iterate(
            unit.id, execute=execute, review=review, recover=recover,
            max_steps=2, max_consecutive_failures=3,
        )
        assert executions == [1, 2, 3]
        assert company._state(unit.id)[0] == "completed"
        assert second.evidence_count > first.evidence_count


def test_review_failure_without_recovery_stops_instead_of_spinning():
    with tempfile.TemporaryDirectory() as tmp:
        company = AutonomousCompany(Path(tmp))
        unit = company.start("review a change", max_iterations=10)
        executions = []

        result = company.iterate(
            unit.id,
            execute=lambda iteration: (executions.append(iteration) or "passed", "built", ()),
            review=lambda iteration: (False, "review rejected", ()),
            max_steps=10,
        )
        assert executions == [1]
        assert company._state(unit.id)[0] == "failed"
        assert any("without a verified recovery path" in event["detail"] for event in _events(company, unit.id))


def test_repeated_failed_review_stops_even_when_recovery_keeps_accepting():
    with tempfile.TemporaryDirectory() as tmp:
        company = AutonomousCompany(Path(tmp))
        unit = company.start("avoid unproductive recursive retry", max_iterations=50)
        executions = []

        result = company.iterate(
            unit.id,
            execute=lambda iteration: (executions.append(iteration) or "passed", "built", ()),
            review=lambda iteration: (False, "unchanged failing assertion", ()),
            recover=lambda problem: (True, "alternate selected but not yet effective", ()),
            max_steps=20,
            max_consecutive_failures=2,
        )
        assert executions == [1, 2]
        assert company._state(unit.id)[0] == "failed"
        assert any("repeated review failure without progress" in event["detail"] for event in _events(company, unit.id))


def test_blocked_work_unit_can_resume_after_clarification_is_resolved():
    with tempfile.TemporaryDirectory() as tmp:
        company = AutonomousCompany(Path(tmp))
        unit = company.start("resume after external blocker", max_iterations=10)
        calls = []

        company.iterate(
            unit.id,
            execute=lambda iteration: (calls.append(iteration) or "blocked", "need operator-provided staging endpoint", ()),
            review=lambda iteration: (True, "unused", ()),
            max_steps=1,
        )
        assert company._state(unit.id)[0] == "waiting_external"
        clarification = company.open_clarifications(unit.id)[0]
        waiting = company.iterate(
            unit.id,
            execute=lambda iteration: (calls.append(iteration) or "passed", "resumed", ()),
            review=lambda iteration: (True, "review passed", ()),
            max_steps=1,
        )
        assert calls == [1]
        company.resolve_clarification(clarification["id"], "staging endpoint provisioned")
        assert company._state(unit.id)[0] == "running"
        company.iterate(
            unit.id,
            execute=lambda iteration: (calls.append(iteration) or "passed", "resumed", ()),
            review=lambda iteration: (True, "review passed", ("review:passed",)),
            max_steps=1,
        )
        assert calls == [1, 2]
        assert company._state(unit.id)[0] == "completed"


def test_ci_wait_is_bounded_and_can_resume_without_reexecuting_work():
    with tempfile.TemporaryDirectory() as tmp:
        company = AutonomousCompany(Path(tmp))
        unit = company.start("resume while waiting for CI", max_iterations=10)
        executions = []
        ci_calls = []

        first = company.iterate(
            unit.id,
            execute=lambda iteration: (executions.append(iteration) or "passed", "built", ()),
            review=lambda iteration: (True, "review passed", ("review:passed",)),
            ci=lambda iteration: (ci_calls.append(iteration) or "pending", "still running", ()),
            poll_seconds=0.001,
            ci_timeout_seconds=1,
            max_ci_polls=1,
            max_steps=1,
        )
        assert company._state(unit.id)[0] == "waiting_ci"
        assert executions == [1]

        second = company.iterate(
            unit.id,
            execute=lambda iteration: (executions.append(iteration) or "passed", "should not rerun", ()),
            review=lambda iteration: (True, "should not rerun", ()),
            ci=lambda iteration: (ci_calls.append(iteration) or "passed", "checks passed", ("ci:passed",)),
            poll_seconds=0.001,
            ci_timeout_seconds=1,
            max_ci_polls=2,
            max_steps=1,
        )
        assert company._state(unit.id)[0] == "completed"
        assert executions == [1]
        assert ci_calls == [1, 1]
        assert second.evidence_count >= first.evidence_count


def _events(company, work_unit_id):
    with company._db() as db:
        rows = db.execute(
            "SELECT kind,status,detail,evidence FROM events WHERE work_unit_id=? ORDER BY rowid",
            (work_unit_id,),
        ).fetchall()
    import json
    return [
        {"kind": row[0], "status": row[1], "detail": row[2], "evidence": tuple(json.loads(row[3]))}
        for row in rows
    ]
