from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from portable.local_offload import ResourceBudget


MODULE = Path(__file__).resolve().parents[1] / ".ai-harness" / "runtime" / "graph_agent_team.py"
SPEC = importlib.util.spec_from_file_location("auren_graph_agent_team", MODULE)
GRAPH_TEAM = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = GRAPH_TEAM
SPEC.loader.exec_module(GRAPH_TEAM)


class GraphResourceExecutionTests(unittest.TestCase):
    def test_read_only_deterministic_work_selects_local_lane(self):
        team = GRAPH_TEAM.GraphAgentTeam(
            [GRAPH_TEAM.AgentSpec("verifier", "verifier", local_command=("python", "-c", "print('ok')"))],
            resource_budget=ResourceBudget(max_workers=1, timeout_seconds=10),
        )
        broker = GRAPH_TEAM.LocalOffloadBroker(Path.cwd(), budget=ResourceBudget(max_workers=1, timeout_seconds=10))
        decision = team._resource_decision(team.agents["verifier"], broker)
        self.assertEqual(decision.lane, "local")
        self.assertEqual(decision.command[:2], ("python", "-c"))

    def test_mutating_agent_stays_on_agent_lane(self):
        team = GRAPH_TEAM.GraphAgentTeam(
            [GRAPH_TEAM.AgentSpec("builder", "builder", read_only=False, local_command=("python", "-c", "print('ok')"))],
            resource_budget=ResourceBudget(max_workers=1, timeout_seconds=10),
        )
        broker = GRAPH_TEAM.LocalOffloadBroker(Path.cwd(), budget=ResourceBudget(max_workers=1, timeout_seconds=10))
        decision = team._resource_decision(team.agents["builder"], broker)
        self.assertEqual(decision.lane, "agent")

    def test_local_result_is_bounded_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = GRAPH_TEAM.SharedTaskMemory(root / "state" / "memory.jsonl", "digest")
            team = GRAPH_TEAM.GraphAgentTeam(
                [GRAPH_TEAM.AgentSpec("verifier", "verifier", local_command=("python", "-c", "print('ok')"))],
                resource_budget=ResourceBudget(max_workers=1, timeout_seconds=10),
            )
            broker = GRAPH_TEAM.LocalOffloadBroker(root, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
            decision = team._resource_decision(team.agents["verifier"], broker)
            result = team._run_local(team.agents["verifier"], decision, memory, broker)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.status, "passed")
            self.assertIn("ok", result.output)

    def test_historical_successes_adapt_routing_estimates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = GRAPH_TEAM.AgentSpec(
                "verifier", "verifier", local_command=("python", "-c", "print('ok')"),
                estimated_duration_seconds=10, estimated_memory_mb=128, evidence_value=0.9,
            )
            steward = GRAPH_TEAM.LearningSteward(root, run_id="seed", task="historical routing")
            key = GRAPH_TEAM.HistoricalResourceRouter.routing_key(agent)
            for i in range(5):
                steward.record_resource_outcome(
                    routing_key=key, status="passed", duration_seconds=90 + i,
                    memory_mb=1024, evidence_yield=0.1, failure_probability=0.0,
                    predicted_duration_seconds=10, predicted_memory_mb=128,
                    predicted_evidence_yield=0.9, evidence_ids=[f"seed-success-{i}"],
                )
            for i in range(12):
                steward.record_resource_outcome(
                    routing_key=key, status="failed", duration_seconds=95,
                    memory_mb=1024, evidence_yield=0.0, failure_probability=0.2,
                    predicted_duration_seconds=10, predicted_memory_mb=128,
                    predicted_evidence_yield=0.9, evidence_ids=[f"seed-failure-{i}"],
                )
            estimate = GRAPH_TEAM.HistoricalResourceRouter(root).estimate(agent)
            self.assertIsNotNone(estimate)
            assert estimate is not None
            self.assertGreater(estimate.duration_seconds, 50)
            self.assertGreater(estimate.memory_mb, 500)
            self.assertGreater(estimate.failure_probability, 0.0)
            broker = GRAPH_TEAM.LocalOffloadBroker(
                root, budget=ResourceBudget(max_workers=1, timeout_seconds=100)
            )
            decision = GRAPH_TEAM.GraphAgentTeam(
                [agent], resource_budget=ResourceBudget(max_workers=1, timeout_seconds=100)
            )._resource_decision(agent, broker)
            self.assertEqual(decision.lane, "agent")
            self.assertEqual(decision.historical["samples"], 17)

    def test_high_pressure_can_fallback_to_agent_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broker = GRAPH_TEAM.LocalOffloadBroker(root, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
            broker._active_jobs = int(broker.capacity()["cpu_count"])
            agent = GRAPH_TEAM.AgentSpec(
                "heavy", "heavy verifier", local_command=("python", "-c", "print('ok')"),
                estimated_duration_seconds=100, estimated_memory_mb=256, evidence_value=0.0,
            )
            decision = GRAPH_TEAM.GraphAgentTeam(
                [agent], resource_budget=ResourceBudget(max_workers=1, timeout_seconds=10)
            )._resource_decision(agent, broker)
            self.assertEqual(decision.lane, "agent")
            self.assertGreater(decision.pressure["cpu_pressure"], 0.0)

if __name__ == "__main__":
    unittest.main()
