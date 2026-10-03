from pathlib import Path

import pytest

from portable.persistent_memory import PersistentMemory
from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.evidence_driven_decision_fabric import EvidenceDrivenDecisionFabric


def fabric(tmp_path):
    memory = PersistentMemory(Path(tmp_path) / "evidence.db", require_approval=False)
    graph = PersistentEvidenceGraph(memory, "hws")
    return graph, EvidenceDrivenDecisionFabric(graph)


def add_observation(graph, capability, digest, *, provider, tool_path, task_family="engineering",
                    success="1", quality="0.95", duration="120", cost="0.2",
                    verified="true", contaminated="false"):
    root = graph.add_node("capability", capability)
    obs = graph.add_node(
        "observation",
        digest,
        {
            "kind": "decision-observation",
            "capability": capability,
            "task_family": task_family,
            "provider": provider,
            "tool_path": tool_path,
            "success": success,
            "quality": quality,
            "duration": duration,
            "cost": cost,
            "verified": verified,
            "contaminated": contaminated,
        },
    )
    graph.add_edge(obs, "supports", root)


def candidates():
    return (
        {"provider": "local", "tool_path": "fast", "verification_depth": "standard",
         "parallel": True, "expected": {"duration": 600, "cost": 0.1, "quality": 0.6, "failure": 0.4}},
        {"provider": "cloud", "tool_path": "deep", "verification_depth": "independent",
         "parallel": False, "expected": {"duration": 900, "cost": 2.0, "quality": 0.7, "failure": 0.3}},
    )


def test_verified_history_changes_route_and_adapts_execution(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(8):
        add_observation(graph, "planner", f"local-{i}", provider="local", tool_path="fast")
    plan = fabric.plan("engineering", "planner", candidates())
    assert plan.selected.provider == "local"
    assert plan.confidence == 0.8
    assert plan.verification_depth == "independent"
    assert plan.retry_budget == 1
    assert plan.resource_lane == "local"
    assert not plan.fallback


def test_contaminated_and_unverified_evidence_is_excluded(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(8):
        add_observation(graph, "planner", f"bad-{i}", provider="cloud", tool_path="deep",
                        verified="false", contaminated="true", success="1", quality="1")
    plan = fabric.plan("engineering", "planner", candidates(), min_confidence=.3)
    assert plan.fallback
    assert plan.evidence_ids == ()


def test_conflicting_history_fails_closed(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(3):
        add_observation(graph, "planner", f"good-{i}", provider="local", tool_path="fast",
                        success="1", quality="1")
        add_observation(graph, "planner", f"bad-{i}", provider="local", tool_path="fast",
                        success="0", quality="0")
    plan = fabric.plan("engineering", "planner", candidates(), min_confidence=.1)
    assert plan.fallback
    assert plan.verification_depth == "deep"
    assert plan.escalation == "human-review"


def test_task_family_isolated_and_digest_deterministic(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(8):
        add_observation(graph, "planner", f"wrong-{i}", provider="cloud", tool_path="deep",
                        task_family="finance")
    p1 = fabric.plan("engineering", "planner", candidates())
    p2 = fabric.plan("engineering", "planner", candidates())
    assert p1.fallback
    assert p1.decision_digest == p2.decision_digest


def test_resource_budget_and_failure_history_change_retry(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(8):
        add_observation(graph, "planner", f"risky-{i}", provider="cloud", tool_path="deep",
                        success="1", quality=".96", duration="1500", cost="3")
    plan = fabric.plan("engineering", "planner", candidates(), duration_budget=2000, max_retries=3)
    assert plan.selected.provider == "cloud"
    assert plan.retry_budget == 2
    assert plan.escalation == "none"
    assert plan.resource_lane == "cloud"
    assert plan.selected.expected_duration <= plan.duration_budget


def test_budget_rejects_strategy_that_cannot_fit(tmp_path):
    graph, fabric = fabric(tmp_path)
    for i in range(8):
        add_observation(graph, "planner", f"slow-{i}", provider="cloud", tool_path="deep",
                        duration="4000")
    with pytest.raises(ValueError, match="duration budget"):
        fabric.plan("engineering", "planner", candidates(), duration_budget=1000)
