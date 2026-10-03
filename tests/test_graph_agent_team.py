import sys
import tempfile
import threading
import unittest
from pathlib import Path

from portable.agency_state_graph import InMemoryCheckpointStore
from portable.task_planner import TaskPlan
from portable.benchmark_task_dispatch import BenchmarkExecutionRequest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".ai-harness"
sys.path.insert(0, str(HARNESS))

from runtime.graph_agent_team import AgentSpec, GraphAgentTeam, SharedTaskMemory, team_for_route


class GraphAgentTeamTests(unittest.TestCase):
    def test_autonomous_campaign_learning_is_runtime_bound_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            request = BenchmarkExecutionRequest(
                task_id="benchmark:coding:1", domain="coding", holdout=True,
                objective="test coding", risk_budget=.2, resource_budget=.5,
                required_evidence=("canonical execution evidence",),
            )
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("verifier", "verifier", depends_on=("planner",)),
            ])
            result = team.execute(
                task="root benchmark campaign",
                intent_digest="intent-a",
                base_prompt="base",
                memory=memory,
                invoke_agent=invoke,
                benchmark_domain="coding",
                benchmark_holdout=True,
                benchmark_execution_task_id=request.task_id,
                benchmark_execution_request=request,
                autonomous_benchmark_learning=True,
                autonomous_benchmark_max_tasks=1,
                autonomous_benchmark_retest_tasks=1,
                safety_evidence_verified=True,
            )
            self.assertIn("autonomous_campaign_learning", result)
            self.assertEqual(result["autonomous_campaign_learning"]["initial"]["selected_task_ids"], [request.task_id])
            self.assertTrue(result["autonomous_campaign_learning"]["initial"]["observations"])
            self.assertIn("verifier", calls)

    def test_shared_memory_is_scoped_to_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            first = SharedTaskMemory(path, "intent-a")
            second = SharedTaskMemory(path, "intent-b")
            first.publish(agent="planner", role="planner", kind="fact", text="only task A", confidence=1.0)
            self.assertEqual(len(first.snapshot()), 1)
            self.assertEqual(second.snapshot(), [])

    def test_dependency_levels_and_parallel_read_only_roles(self):
        team = GraphAgentTeam([
            AgentSpec("planner", "planner"),
            AgentSpec("explorer", "explorer", depends_on=("planner",)),
            AgentSpec("researcher", "researcher", depends_on=("planner",)),
            AgentSpec("builder", "builder", depends_on=("explorer", "researcher"), read_only=False),
        ])
        levels = [[agent.name for agent in level] for level in team.levels()]
        self.assertEqual(levels[0], ["planner"])
        self.assertEqual(set(levels[1]), {"explorer", "researcher"})
        self.assertEqual(levels[2], ["builder"])

    def test_graph_plan_is_canonical_task_plan(self):
        team = GraphAgentTeam([
            AgentSpec("planner", "planner"),
            AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
        ])
        self.assertIsInstance(team._plan, TaskPlan)
        self.assertEqual(team._plan.tasks["builder"].dependencies, ["planner"])
        self.assertEqual([[agent.name for agent in level] for level in team.levels()], [["planner"], ["builder"]])

    def test_team_passes_shared_memory_to_downstream_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            seen = {}

            def invoke(agent, prompt):
                seen[agent.name] = prompt
                return 0, f"finding from {agent.name}", 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            result = team.execute(task="implement X", intent_digest="intent-a", base_prompt="base", memory=memory, invoke_agent=invoke)
            self.assertTrue(result["accepted"])
            self.assertIn("finding from planner", seen["builder"])
            self.assertEqual(result["shared_memory_entries"], 2)
            self.assertEqual(result["execution_trace"], ["planner", "builder"])

    def test_read_only_dependencies_run_in_parallel_through_state_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            active = 0
            peak = 0
            lock = threading.Lock()
            rendezvous = threading.Barrier(2, timeout=2.0)

            def invoke(agent, prompt):
                nonlocal active, peak
                if agent.name in {"explorer", "researcher"}:
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    try:
                        rendezvous.wait()
                    finally:
                        with lock:
                            active -= 1
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("explorer", "explorer", depends_on=("planner",)),
                AgentSpec("researcher", "researcher", depends_on=("planner",)),
                AgentSpec("builder", "builder", depends_on=("explorer", "researcher"), read_only=False),
            ], max_parallel_read_only=2)
            result = team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory, invoke_agent=invoke)
            self.assertTrue(result["accepted"])
            self.assertEqual(peak, 2)

    def test_checkpoint_resume_does_not_repeat_completed_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")
            store = InMemoryCheckpointStore()
            calls = []

            def invoke(agent, prompt):
                calls.append(agent.name)
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner"),
                AgentSpec("builder", "builder", depends_on=("planner",), read_only=False),
            ])
            with self.assertRaises(RuntimeError):
                team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                             invoke_agent=invoke, checkpoint=store, run_id="resume-1", max_steps=1)
            self.assertEqual(calls, ["planner"])
            resumed = team.execute(task="X", intent_digest="intent-a", base_prompt="base", memory=memory,
                                   invoke_agent=invoke, checkpoint=store, resume=True, run_id="resume-1")
            self.assertTrue(resumed["accepted"])
            self.assertEqual(calls, ["planner", "builder"])
            self.assertEqual(resumed["execution_trace"], ["planner", "builder"])

    def test_route_creates_builder_verifier_and_reviews(self):
        team = team_for_route({"mode": "implement", "capabilities": [], "risk": "high"})
        names = set(team.agents)
        self.assertTrue({"planner", "explorer", "builder", "verifier", "correctness-reviewer", "security-reviewer", "architecture-reviewer", "synthesizer"}.issubset(names))

    def test_resource_routing_exposes_adaptive_inference_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = __import__("portable.local_offload", fromlist=["LocalOffloadBroker"]).LocalOffloadBroker(
                Path(tmp),
            )
            team = GraphAgentTeam([
                AgentSpec(
                    "verifier",
                    "verifier",
                    local_command=("python", "-m", "pytest", "-q"),
                    estimated_duration_seconds=5.0,
                    estimated_memory_mb=128,
                    evidence_value=0.95,
                ),
            ])
            decision = team._resource_decision(team.agents["verifier"], broker)
            self.assertIn(decision.inference_depth, {"minimal", "standard", "deep", "human"})
            self.assertEqual(decision.inference_depth, "standard")

    def test_cycle_is_rejected(self):
        with self.assertRaises(ValueError):
            GraphAgentTeam([
                AgentSpec("a", "a", depends_on=("b",)),
                AgentSpec("b", "b", depends_on=("a",)),
            ])



    def test_skill_execution_plan_telemetry_is_bounded_and_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = SharedTaskMemory(Path(tmp) / "memory.jsonl", "intent-a")

            def invoke(agent, prompt):
                return 0, agent.name, 0.01

            team = GraphAgentTeam([
                AgentSpec("planner", "planner", capabilities=("delegate_task",)),
            ])
            result = team.execute(
                task="plan X",
                intent_digest="intent-a",
                base_prompt="base",
                memory=memory,
                invoke_agent=invoke,
            )
            plan = result["agents"]["planner"]["capability_execution_plan"]
            self.assertEqual(plan["selected_skill_count"], plan["executed_skill_count"])
            self.assertEqual(plan["pruned_secondary_count"], 0)
            self.assertLessEqual(plan["instruction_chars"], plan["context_budget_chars"])
            self.assertEqual(plan["planned_parallelism"], 1)
            self.assertEqual(len(plan["groups"]), 1)

if __name__ == "__main__":
    unittest.main()
