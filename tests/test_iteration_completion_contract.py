from portable.autonomous_company import AutonomousCompany

def test_clarification_is_held_and_recorded(tmp_path):
    company = AutonomousCompany(tmp_path)
    work = company.start("complete a multi-part task")
    cid = company.hold_clarification(work.id, "Which provider should be used?", dependency="provider choice")
    pending = company.open_clarifications(work.id)
    assert pending[0]["id"] == cid
    assert pending[0]["state"] == "open"

def test_blocked_iteration_can_continue_independent_work(tmp_path):
    company = AutonomousCompany(tmp_path)
    work = company.start("complete independent branches", max_iterations=3)
    calls = []
    def execute(i):
        calls.append(i)
        if i == 1:
            return "blocked", "provider decision required; independent validation remains", ("independent:validation",)
        return "completed", "independent work completed", ("validation:passed",)
    def review(i):
        return True, "review passed", ("review:passed",)
    def recover(detail):
        return len(calls) < 3, "continue independent work while clarification remains held", ("clarification:held",)
    company.iterate(work.id, execute=execute, review=review, recover=recover)
    assert calls == [1, 2]
