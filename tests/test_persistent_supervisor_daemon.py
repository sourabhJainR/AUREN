from portable.orchestration import Node, NodeKind, Graph
from portable.state_graph import StateGraph, GraphAgentTeam
from portable.autonomous_daemon import PersistentSupervisorDaemon

def test_explicit_state_graph_and_team_personas():
    graph = StateGraph.from_nodes([
        Node("plan", NodeKind.DETERMINISTIC, lambda s: "planned"),
        Node("review", NodeKind.EVALUATOR, lambda s: True, depends_on=("plan",)),
    ])
    team = GraphAgentTeam(graph)
    assert graph.digest()
    assert "pm" in team.required_personas("deliver secure database-backed web UI")
    assert "security_privacy" in team.required_personas("deliver secure database-backed web UI")
    assert "dba" in team.required_personas("deliver secure database-backed web UI")
    assert "frontend" in team.required_personas("deliver secure database-backed web UI")

def test_daemon_checkpoint_detects_and_recovers_stale(tmp_path):
    daemon = PersistentSupervisorDaemon(tmp_path, stale_after_seconds=1, checkpoint_seconds=1)
    assert daemon.last_checkpoint is None
    checkpoint = daemon.checkpoint()
    assert checkpoint.healthy
    assert checkpoint.reviewed >= 0
