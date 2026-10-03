from pathlib import Path
import importlib.util
import sys
import unittest

from portable.persistent_memory import PersistentMemory
from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.local_offload import LocalOffloadBroker, ResourceBudget


MODULE = Path(__file__).resolve().parents[1] / ".ai-harness" / "runtime" / "graph_agent_team.py"
SPEC = importlib.util.spec_from_file_location("graph_agent_team_evidence_test", MODULE)
GRAPH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = GRAPH
SPEC.loader.exec_module(GRAPH)


class GraphEvidenceDecisionTests(unittest.TestCase):
    def _team(self, root):
        return GRAPH.GraphAgentTeam(
            [GRAPH.AgentSpec(
                "verifier", "verifier",
                local_command=("python", "-c", "print('ok')"),
                estimated_duration_seconds=30,
                estimated_memory_mb=256,
            )],
            resource_budget=ResourceBudget(max_workers=1, timeout_seconds=600),
        ), LocalOffloadBroker(root, budget=ResourceBudget(max_workers=1, timeout_seconds=600))

    def _observation(self, graph, capability, digest, provider, tool_path, success, quality):
        root = graph.add_node("capability", capability)
        obs = graph.add_node("observation", digest, {
            "kind": "decision-observation",
            "capability": capability,
            "task_family": "verifier",
            "provider": provider,
            "tool_path": tool_path,
            "success": str(success),
            "quality": str(quality),
            "duration": "30",
            "cost": "0.2" if provider == "local" else "1.0",
            "verified": "true",
            "contaminated": "false",
        })
        graph.add_edge(obs, "supports", root)

    def test_cold_start_is_conservative(self):
        with self.subTest("no trusted history"):
            with __import__("tempfile").TemporaryDirectory() as tmp:
                root = Path(tmp)
                team, broker = self._team(root)
                plan = team._evidence_plan(team.agents["verifier"], broker)
                self.assertTrue(plan["fallback"])
                self.assertEqual(plan["confidence"], 0.0)

    def test_verified_history_can_override_existing_resource_counterfactual(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / ".aer" / "memory.db"
            graph = PersistentEvidenceGraph(PersistentMemory(db, require_approval=False), "hws")
            local_tool = "python:-c:print('ok')"
            for i in range(8):
                self._observation(graph, "verifier", f"agent-{i}", "agent", "agent", 1, .98)
            team, broker = self._team(root)
            decision = team._resource_decision(team.agents["verifier"], broker)
            self.assertEqual(decision.lane, "agent")
            self.assertFalse(decision.evidence_plan["fallback"])
            self.assertGreaterEqual(decision.evidence_plan["confidence"], .3)
            self.assertIn("persistent verified evidence", decision.reason)

    def test_contaminated_history_cannot_steer_execution(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / ".aer" / "memory.db"
            graph = PersistentEvidenceGraph(PersistentMemory(db, require_approval=False), "hws")
            cap = graph.add_node("capability", "verifier")
            for i in range(8):
                obs = graph.add_node("observation", f"contaminated-{i}", {
                    "kind": "decision-observation", "capability": "verifier",
                    "task_family": "verifier", "provider": "agent", "tool_path": "agent",
                    "success": "1", "quality": "1", "duration": "1", "cost": "0",
                    "verified": "true", "contaminated": "true",
                })
                graph.add_edge(obs, "supports", cap)
            team, broker = self._team(root)
            plan = team._evidence_plan(team.agents["verifier"], broker)
            self.assertTrue(plan["fallback"])


if __name__ == "__main__":
    unittest.main()
