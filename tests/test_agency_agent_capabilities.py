from pathlib import Path

from portable.agency_agent_capabilities import (
    AgentEvent,
    AgentNode,
    AllowlistedToolPolicy,
    CollaborationGraph,
    ContextItem,
    EventRuntime,
    MemoryFact,
    MemoryStore,
    ToolRegistry,
    ToolSpec,
    build_context,
)


def test_context_budget_is_bounded_and_stable_items_win():
    pack = build_context([ContextItem("stable", "a b", 10), ContextItem("large", "x " * 20, 1)], 5)
    assert pack.items[0].key == "stable"
    assert "large" in pack.omitted
    assert pack.digest


def test_memory_conflict_prefers_higher_confidence():
    store = MemoryStore()
    store.upsert(MemoryFact("language", "Python", confidence=0.9))
    store.upsert(MemoryFact("language", "Ruby", confidence=0.4))
    assert store.get("language").value == "Python"


def test_memory_can_reload_from_durable_store(tmp_path: Path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.upsert(MemoryFact("language", "Python", kind="semantic", confidence=0.9))
    restored = MemoryStore(path)
    assert restored.get("language").value == "Python"


def test_tool_permissions_are_fail_closed():
    registry = ToolRegistry([ToolSpec("deploy", "deploy artifact", "execution", permissions=("deploy",), handler=lambda: "ok")])
    assert registry.execute("deploy", {}, AllowlistedToolPolicy(allowed_permissions=frozenset({"deploy"}))) == "ok"
    try:
        registry.execute("deploy", {}, AllowlistedToolPolicy())
    except PermissionError:
        pass
    else:
        raise AssertionError("tool without required permission must be denied")


def test_active_tool_discovery_and_policy():
    registry = ToolRegistry([ToolSpec("search", "search repository files", "perception", handler=lambda q: q)])
    tool = registry.discover("repository search")[0]
    assert tool.name == "search"
    assert registry.execute("search", {"q": "AUREN"}, AllowlistedToolPolicy()) == "AUREN"


def test_event_runtime_supports_cancellation():
    seen = []
    runtime = EventRuntime()
    runtime.on("timer", lambda event: seen.append(event.event_id))
    runtime.dispatch(AgentEvent("e1", "timer", {}))
    runtime.cancel("e2")
    runtime.dispatch(AgentEvent("e2", "timer", {}))
    assert seen == ["e1"]


def test_multi_agent_handoff_preserves_explicit_context_keys():
    graph = CollaborationGraph()
    graph.add_agent(AgentNode("planner", "planner"))
    graph.add_agent(AgentNode("builder", "builder"))
    handoff = graph.handoff("planner", "builder", "implement", ("contract", "evidence"), ("ev1",))
    assert handoff.target == "builder"
    assert graph.topology()["handoffs"][0]["context_keys"] == ("contract", "evidence")
