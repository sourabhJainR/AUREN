import tempfile
import unittest
from pathlib import Path

from portable.orchestration import (
    Graph,
    Node,
    NodeKind,
    NodeStatus,
    Orchestrator,
    PromotionStatus,
    RunStatus,
    SelfModificationEngine,
    execution_strategy,
)


class OrchestrationTests(unittest.TestCase):
    def test_graph_rejects_cycles(self):
        nodes = [
            Node("a", NodeKind.DETERMINISTIC, lambda _: 1, depends_on=("b",)),
            Node("b", NodeKind.DETERMINISTIC, lambda _: 1, depends_on=("a",)),
        ]
        with self.assertRaises(ValueError):
            Graph(nodes)

    def test_graph_executes_dependencies_before_dependents(self):
        seen = []
        graph = Graph([
            Node("build", NodeKind.AGENT, lambda _: seen.append("build") or "ok"),
            Node("verify", NodeKind.EVALUATOR, lambda state: seen.append("verify") or state["build"] == "ok", depends_on=("build",)),
        ])
        run = Orchestrator(graph).run("task-1", "ship the change")
        self.assertEqual(run.status, RunStatus.ACCEPTED)
        self.assertEqual(seen, ["build", "verify"])
        self.assertTrue(run.intent_digest)
        self.assertTrue(run.environment_fingerprint)
        self.assertTrue(run.trajectory)

    def test_adaptive_policy_changes_future_agent_attempt_budget(self):
        attempts = []
        graph = Graph([
            Node(
                "agent",
                NodeKind.AGENT,
                lambda _: attempts.append(len(attempts) + 1) or "bad",
                max_attempts=5,
                evaluator=lambda output: output == "good",
                repair=lambda output, _: output,
            )
        ])
        run = Orchestrator(graph).run(
            "adaptive-task",
            "fix repeated regression",
            context={
                "auren_adaptive_policy": {
                    "version": "v42",
                    "strategy": "evidence-first",
                    "iteration_target": 2.0,
                }
            },
        )
        result = run.results["agent"]
        self.assertEqual(result.status, NodeStatus.FAILED)
        self.assertEqual(result.attempts, 2)
        applied = [item for item in run.trajectory if item.get("event") == "adaptive_policy_applied"][0]
        self.assertEqual(applied["version"], "v42")
        self.assertEqual(applied["strategy"], "evidence-first")
        self.assertEqual(applied["iteration_target"], 2.0)
        self.assertTrue(applied["strategy_known"])
        node_events = [item for item in run.trajectory if item.get("node") == "agent"]
        self.assertEqual(node_events[0]["attempt_limit"], 2)

    def test_execution_strategy_profiles_are_typed_and_unknown_names_are_safe(self):
        deep = execution_strategy("deep-verify")
        self.assertTrue(deep.known)
        self.assertEqual(deep.verification_depth, "independent")
        self.assertEqual(deep.resource_lane, "agent")
        self.assertGreater(deep.attempt_multiplier, 1.0)
        unknown = execution_strategy("untrusted-new-strategy")
        self.assertFalse(unknown.known)
        self.assertEqual(unknown.name, "default")
        self.assertEqual(unknown.verification_depth, "standard")

    def test_strategy_profile_changes_bounded_agent_attempt_budget(self):
        graph = Graph([
            Node(
                "agent", NodeKind.AGENT, lambda _: "bad", max_attempts=3,
                evaluator=lambda output: output == "good", repair=lambda output, _: output,
            )
        ])
        run = Orchestrator(graph).run(
            "strategy-task", "apply learned strategy", context={
                "auren_adaptive_policy": {
                    "version": "v-strategy", "strategy": "deep-verify", "iteration_target": 2.0,
                }
            }
        )
        self.assertEqual(run.results["agent"].attempts, 3)
        applied = [item for item in run.trajectory if item.get("event") == "adaptive_policy_applied"][0]
        self.assertEqual(applied["verification_depth"], "independent")
        self.assertEqual(applied["resource_lane"], "agent")
        self.assertEqual(applied["minimum_evidence"], 2)

    def test_adaptive_policy_never_expands_static_safety_budget(self):
        attempts = []
        graph = Graph([
            Node(
                "agent",
                NodeKind.AGENT,
                lambda _: attempts.append(1) or "bad",
                max_attempts=2,
                evaluator=lambda output: output == "good",
                repair=lambda output, _: output,
            )
        ])
        run = Orchestrator(graph).run(
            "adaptive-task-2",
            "preserve safety budget",
            context={
                "auren_adaptive_policy": {
                    "version": "v43",
                    "strategy": "default",
                    "iteration_target": 32.0,
                }
            },
        )
        self.assertEqual(run.results["agent"].attempts, 2)

    def test_invalid_adaptive_policy_is_ignored(self):
        graph = Graph([
            Node(
                "agent",
                NodeKind.AGENT,
                lambda _: "bad",
                max_attempts=3,
                evaluator=lambda output: output == "good",
                repair=lambda output, _: output,
            )
        ])
        run = Orchestrator(graph).run(
            "adaptive-task-3",
            "ignore invalid policy",
            context={"auren_adaptive_policy": {"version": "broken", "strategy": "x", "iteration_target": 0}},
        )
        self.assertEqual(run.results["agent"].attempts, 3)

    def test_agent_loop_requires_evidence_and_bounds_retries(self):
        attempts = []
        graph = Graph([
            Node(
                "agent",
                NodeKind.AGENT,
                lambda _: attempts.append(len(attempts) + 1) or "bad",
                max_attempts=3,
                evaluator=lambda output: output == "good",
                repair=lambda output, _: "good",
            )
        ])
        run = Orchestrator(graph).run("task-2", "fix regression")
        result = run.results["agent"]
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(result.status, NodeStatus.FAILED)
        self.assertEqual(result.attempts, 3)
        self.assertGreaterEqual(result.repair_count, 1)
        self.assertGreaterEqual(len(result.evidence), 3)
        signal = Orchestrator.learning_signal(run)
        self.assertEqual(signal.attempt_count, 3)
        self.assertGreater(signal.trajectory_digest, "")
        self.assertGreater(signal.transfer_key, "")

    def test_self_improvement_is_candidate_based(self):
        graph = Graph([
            Node("agent", NodeKind.AGENT, lambda _: "ok", evaluator=lambda _: True)
        ])
        run = Orchestrator(graph).run("task-3", "implement feature")
        self.assertEqual(run.status, RunStatus.ACCEPTED)
        self.assertEqual(run.learned_candidates, [])

    def test_replay_is_deterministic_and_does_not_execute_nodes(self):
        calls = []
        graph = Graph([
            Node("agent", NodeKind.AGENT, lambda _: calls.append(1) or "ok")
        ])
        orchestrator = Orchestrator(graph)
        run = orchestrator.run("task-4", "verify replay")
        before = len(calls)
        first = orchestrator.replay_json(run)
        second = orchestrator.replay_json(run)
        self.assertEqual(first, second)
        self.assertEqual(before, len(calls))
        self.assertIn("trajectory", first)
        self.assertIn("graph_digest", first)

    def test_self_modification_requires_both_gates_before_activation(self):
        source = '''\nfrom portable.orchestration import Graph, Node, NodeKind\n\ndef build_graph():\n    return Graph([Node("learned", NodeKind.DETERMINISTIC, lambda _: "new")])\n'''
        with tempfile.TemporaryDirectory() as tmp:
            engine = SelfModificationEngine(Path(tmp))
            candidate = engine.propose(source, "parent-digest", "learned better routing")
            rejected = engine.evaluate_and_promote(candidate, regression_gate=lambda _: True, safety_gate=lambda _: False)
            self.assertEqual(rejected.status, PromotionStatus.SAFETY_FAILED)
            self.assertFalse((Path(tmp) / "active.py").exists())

            promoted = engine.evaluate_and_promote(candidate, regression_gate=lambda _: True, safety_gate=lambda _: True)
            self.assertEqual(promoted.status, PromotionStatus.PROMOTED)
            self.assertTrue((Path(tmp) / "active.py").exists())

    def test_self_modification_regression_failure_cannot_activate(self):
        source = '''\nfrom portable.orchestration import Graph, Node, NodeKind\n\ndef build_graph():\n    return Graph([Node("candidate", NodeKind.DETERMINISTIC, lambda _: "candidate")])\n'''
        with tempfile.TemporaryDirectory() as tmp:
            engine = SelfModificationEngine(Path(tmp))
            candidate = engine.propose(source, "parent-digest", "candidate")
            result = engine.evaluate_and_promote(candidate, regression_gate=lambda _: False, safety_gate=lambda _: True)
            self.assertEqual(result.status, PromotionStatus.REGRESSION_FAILED)
            self.assertFalse((Path(tmp) / "active.py").exists())

    def test_candidate_validation_does_not_execute_generated_code(self):
        source = '''\nfrom portable.orchestration import Graph, Node, NodeKind\nopen("SHOULD_NOT_EXIST", "w").write("executed")\n\ndef build_graph():\n    return Graph([Node("candidate", NodeKind.DETERMINISTIC, lambda _: "candidate")])\n'''
        with tempfile.TemporaryDirectory() as tmp:
            engine = SelfModificationEngine(Path(tmp))
            candidate = engine.propose(source, "parent-digest", "candidate with top-level side effect")
            self.assertEqual(candidate.status, PromotionStatus.CANDIDATE)
            self.assertFalse((Path(tmp) / "SHOULD_NOT_EXIST").exists())

    def test_candidate_validation_rejects_dangerous_imports(self):
        source = '''\nimport subprocess\nfrom portable.orchestration import Graph, Node, NodeKind\n\ndef build_graph():\n    return Graph([Node("candidate", NodeKind.DETERMINISTIC, lambda _: "candidate")])\n'''
        with tempfile.TemporaryDirectory() as tmp:
            engine = SelfModificationEngine(Path(tmp))
            with self.assertRaises(ValueError):
                engine.propose(source, "parent-digest", "unsafe import")

    def test_self_modification_rolls_back_previous_active_behavior(self):
        first = '''\nfrom portable.orchestration import Graph, Node, NodeKind\n\ndef build_graph():\n    return Graph([Node("first", NodeKind.DETERMINISTIC, lambda _: "first")])\n'''
        second = '''\nfrom portable.orchestration import Graph, Node, NodeKind\n\ndef build_graph():\n    return Graph([Node("second", NodeKind.DETERMINISTIC, lambda _: "second")])\n'''
        with tempfile.TemporaryDirectory() as tmp:
            engine = SelfModificationEngine(Path(tmp))
            a = engine.propose(first, "root", "first")
            engine.evaluate_and_promote(a, regression_gate=lambda _: True, safety_gate=lambda _: True)
            b = engine.propose(second, a.source_digest, "second")
            engine.evaluate_and_promote(b, regression_gate=lambda _: True, safety_gate=lambda _: True)
            engine.rollback()
            self.assertIn('"first"', (Path(tmp) / "active.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
