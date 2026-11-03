from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".ai-harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from runtime import _phase_team
from runtime.graph_agent_team import AgentSpec, GraphAgentTeam


def test_every_provider_phase_has_a_dependency_graph():
    route = {"mode": "implement", "risk": "high"}
    phases = ("context", "research", "poc", "debug", "execute", "repair", "grill", "review")
    for phase in phases:
        team = _phase_team(route, phase, (AgentSpec, GraphAgentTeam))
        assert len(team.agents) >= 4
        assert "planner" in team.agents
        assert "explorer" in team.agents
        assert "synthesizer" in team.agents
        assert team.levels()


def test_mutation_phases_have_serialized_builder_before_verifier():
    route = {"mode": "implement", "risk": "low"}
    for phase in ("execute", "debug", "poc", "repair"):
        team = _phase_team(route, phase, (AgentSpec, GraphAgentTeam))
        assert "builder" in team.agents
        assert "verifier" in team.agents
        assert "builder" in team.agents["verifier"].depends_on
        assert team.agents["builder"].read_only is False
        assert team.agents["verifier"].read_only is True


def test_high_risk_phases_add_security_and_architecture_review():
    route = {"mode": "implement", "risk": "critical"}
    for phase in ("execute", "debug", "poc", "repair", "review", "grill"):
        team = _phase_team(route, phase, (AgentSpec, GraphAgentTeam))
        assert "security-reviewer" in team.agents
        assert "architecture-reviewer" in team.agents
        assert "security-reviewer" in team.agents["synthesizer"].depends_on
        assert "architecture-reviewer" in team.agents["synthesizer"].depends_on


def test_phase_agents_use_role_specific_private_memory_profiles():
    team = _phase_team({"mode": "implement", "risk": "critical"}, "execute", (AgentSpec, GraphAgentTeam))
    assert team.agents["builder"].profile_id == "backend_implementer"
    assert team.agents["verifier"].profile_id == "qa_reviewer"
    assert team.agents["security-reviewer"].profile_id == "security_reviewer"
    assert team.agents["architecture-reviewer"].profile_id == "principal_architect"
    assert all(agent.profile_revision == 1 for agent in team.agents.values())


def test_graph_bridge_uses_durable_manifest_run_id_for_private_memory(tmp_path, monkeypatch):
    import json
    import logging
    import engine

    captured = {}

    def fake_execute(self, **kwargs):
        captured["run_id"] = kwargs.get("run_id")
        return {"accepted": True, "agents": {}}

    monkeypatch.setattr(GraphAgentTeam, "execute", fake_execute)
    run_dir = tmp_path / "stable-run-42"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "stable-run-42",
        "task_id": "task-42",
        "task": "verify durable profile memory scope",
        "intent_digest": "a" * 64,
        "route": {"mode": "implement", "risk": "critical"},
    }), encoding="utf-8")
    prompt_file = run_dir / "execute.prompt.md"
    prompt_file.write_text("execute the task", encoding="utf-8")

    engine.invoke(
        {"name": "test-provider"},
        prompt_file,
        "execute",
        run_dir,
        10,
        True,
        logging.getLogger("graph-profile-memory-test"),
    )

    assert captured["run_id"] == "stable-run-42"
