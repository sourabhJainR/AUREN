import sqlite3
from pathlib import Path
from portable.agent_capabilities import Skill, SkillRegistry
from portable.autonomous_company import AutonomousCompany
from portable.executive_team import ExecutiveTeam, RecipeMiner, SkillAgent

def test_company_persists_resume_and_trust(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("ship a feature")
    company.record(unit.id, "execution", "passed", "implementation complete", ["impl"])
    company.record(unit.id, "verification", "passed", "tests passed", ["tests"])
    company.record(unit.id, "ci", "passed", "CI passed", ["ci"])
    score = company.finish(unit.id, success=True, reason="done", evidence=["release"])
    assert score.score > 0.5
    assert company.resume(unit.id).id == unit.id

def test_blocker_selects_confirmed_alternate(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("recover")
    def confirm(candidate):
        if candidate == "alternate-2":
            return True, "validated", ["alternate-2"]
        return False, "rejected", ["alternate-1"]
    decision = company.blocker(unit.id, "primary blocked", alternatives=("alternate-1", "alternate-2"), confirm=confirm)
    assert decision.selected_alternate == "alternate-2"

def test_recipe_miner_extracts_rules(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text('def verify():\n    """Verification must fail closed and preserve evidence."""\n    return True\n')
    recipes = RecipeMiner(tmp_path).extract()
    assert any("fail closed" in r.recipe.lower() for r in recipes)

def test_executive_team_shares_trust(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("team task")
    registry = SkillRegistry()
    registry.register(Skill("verify", "verification", "verify evidence"))
    team = ExecutiveTeam(company, registry)
    def run(context):
        return "passed", "verified", ("e1",)
    team.register(SkillAgent("verifier", "verifier", ("verify",), run))
    status, _, _ = team.run_agent(unit.id, "verifier", "task")
    assert status == "passed"
    assert "score" in team.share_trust(unit.id)



def test_company_refuses_success_without_current_verification_evidence(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("require evidence before completion")

    company.finish(unit.id, success=True, reason="caller claimed completion", evidence=("release:claimed",))

    assert company._state(unit.id)[0] == "failed"
    with sqlite3.connect(company.db_path) as db:
        latest = db.execute(
            "SELECT kind,status,detail FROM events WHERE work_unit_id=? ORDER BY rowid DESC LIMIT 2",
            (unit.id,),
        ).fetchall()
    assert ("verification", "failed") in [(row[0], row[1]) for row in latest]
    assert "verification evidence is missing" in latest[0][2] or "verification evidence is missing" in latest[1][2]


def test_company_refuses_success_with_empty_verification_evidence(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("require non-empty verification evidence")
    company.record(unit.id, "verification", "passed", "tests were reported", ())

    company.finish(unit.id, success=True, reason="caller claimed completion", evidence=("release:claimed",))

    assert company._state(unit.id)[0] == "failed"


def test_company_refuses_success_with_stale_iteration_verification(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("require verification for the current iteration")
    company.record(unit.id, "verification", "passed", "earlier iteration passed", ("tests:old",))
    company._transition(unit.id, "running", 1)

    company.finish(unit.id, success=True, reason="caller claimed completion", evidence=("release:claimed",))

    assert company._state(unit.id)[0] == "failed"


def test_company_finish_accepts_current_verified_evidence(tmp_path: Path):
    company = AutonomousCompany(tmp_path)
    unit = company.start("complete only after current verification")
    company.record(unit.id, "verification", "passed", "current verification passed", ("tests:current",))

    company.finish(unit.id, success=True, reason="verified completion")

    assert company._state(unit.id)[0] == "completed"
    with sqlite3.connect(company.db_path) as db:
        latest = db.execute(
            "SELECT evidence FROM events WHERE work_unit_id=? AND kind='execution' ORDER BY rowid DESC LIMIT 1",
            (unit.id,),
        ).fetchone()
    assert "tests:current" in latest[0]
