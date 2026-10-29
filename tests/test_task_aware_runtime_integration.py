"""Integration tests for AdaptiveRuntime task-aware routing."""
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.agent_capabilities import PersistentMemory
from portable.automation_scheduler import AutomationScheduler
from portable.orchestration import Graph, Node, NodeKind
from portable.session_state import SessionStore
from portable.task_aware_routing import ResourceCandidate


class TaskAwareRuntimeIntegrationTests(unittest.TestCase):
    def test_runtime_routes_memory_and_resources_through_canonical_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = PersistentMemory(root / "memory.db", require_approval=True)
            scheduler = AutomationScheduler(root / "automation.db")
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime = AdaptiveRuntime(
                graph, persistent_memory=memory, automation_scheduler=scheduler,
                session_store=SessionStore(root / "sessions.db"),
            )
            try:
                saved = runtime.memory_orchestrator(root).remember(
                    "procedural", "verified rule: reconcile unknown remote outcomes before replay",
                    source="approved-run", approved=True, verified=True, confidence=0.95,
                )
                candidate = ResourceCandidate(
                    name="safe-code-search", source="builtin",
                    capabilities=frozenset({"repository_search"}), available=True,
                    permission_granted=True, privacy_compatible=True, risk="low",
                    estimated_cost=0.1, estimated_latency_ms=80,
                    measured_quality=0.9, failure_rate=0.02,
                )
                routed = runtime.route_task_context(
                    root, "debug remote outcome recovery",
                    memory_options={
                        "task_type": "debugging",
                        "required_memory_ids": (saved.memory_id,),
                        "budget_chars": 500,
                    },
                    resource_candidates=(candidate,),
                    required_capabilities=("repository_search",),
                    max_risk="medium",
                )
                self.assertIn(saved.memory_id, routed["receipt"].memory_ids)
                self.assertIn("reconcile unknown remote outcomes", routed["recall"].text)
                self.assertEqual(("safe-code-search",), routed["resources"].selected)
                self.assertEqual("debugging", routed["plan"].task_type)
                self.assertNotIn("reconcile unknown remote outcomes", repr(routed["receipt"]))
            finally:
                scheduler.close()
                memory.close()


if __name__ == "__main__":
    unittest.main()
