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
