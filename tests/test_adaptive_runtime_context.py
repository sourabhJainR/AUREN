import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.automation_scheduler import AutomationScheduler
from portable.context_graph import ContextGraph, ContextNode
from portable.hypothesis_engine import BeliefEvidence, Hypothesis
from portable.persistent_memory import PersistentMemory
from portable.adaptive_tuning import AdaptivePolicy
from portable.orchestration import Graph, Node, NodeKind
from portable.session_state import SessionStore


class FailingMemory(PersistentMemory):
    def remember(self, *args, **kwargs):
        raise RuntimeError("forced memory failure")


class AdaptiveRuntimeContextTests(unittest.TestCase):
    def _runtime(self, root: Path, graph: Graph, *, memory=None):
        memory = memory or PersistentMemory(root / "memory.db", require_approval=False)
        scheduler = AutomationScheduler(root / "automation.db")
        sessions = SessionStore(root / "sessions.db")
        return AdaptiveRuntime(graph, persistent_memory=memory, automation_scheduler=scheduler, session_store=sessions), memory, scheduler

    def test_runtime_resolves_context_without_bypassing_orchestrator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured = {}
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: captured.update(context) or "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            project_key = runtime.session_store.project_key(root)
            context_graph = ContextGraph(memory, project_key)
            context_graph.upsert_node(ContextNode("task:1", "task", "Fix timeout", "task"))
            context_graph.upsert_node(ContextNode("file:1", "file", "client.py", "repo"))
            context_graph.link("task:1", "touches", "file:1", source="repo")
            resolution = runtime.resolve_context(root, "Fix timeout", node_id="task:1")
            self.assertIn("client.py", resolution.pack)
            result = runtime.run(session_id="s1", task_id="t1", project_root=root, intent="Fix timeout", context_node_id="task:1", enrich_context=True)
            self.assertEqual(result.status.value, "accepted")
            self.assertIn("client.py", captured["auren_context_pack"])
            self.assertTrue(captured["auren_context_digest"])
            self.assertIsNotNone(runtime.last_cognitive_episode)
            self.assertEqual(runtime.last_cognitive_episode.status, "accepted")
            self.assertIn("evaluate", runtime.last_cognitive_episode.phases)
            memory_hits = memory.search(project_key, "execution_completed", limit=5)
            self.assertTrue(memory_hits)
            scheduler.close()
            memory.close()

    def test_memory_orchestration_is_owned_by_runtime_and_shares_canonical_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            project_key = runtime.session_store.project_key(root)
            orchestrator = runtime.memory_orchestrator(root)
            self.assertIs(orchestrator, runtime.memory_orchestrator(root))
            self.assertEqual(memory.path, orchestrator.graph.memory.path)
            saved = orchestrator.remember(
                "semantic", "repository uses explicit bounded memory budgets",
                source="architecture", approved=True, verified=True,
            )
            packed = runtime.pack_memory_context(
                root, "bounded memory budgets", required_memory_ids=(saved.memory_id,),
            )
            self.assertIn("repository uses explicit bounded memory budgets", packed)
            self.assertIn(saved.memory_id, packed)
            self.assertTrue(memory.search(project_key, "bounded memory budgets", limit=5))
            scheduler.close()
            memory.close()

    def test_runtime_proactively_recommends_repository_native_design(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "setup.py").write_text("# Python package\n", encoding="utf-8")
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            recommendation = runtime.recommend_design(
                root, "resume quota exhausted work safely", risk="high",
            )
            self.assertIn("Python", recommendation["stack"]["languages"])
            patterns = [item["pattern"] for item in recommendation["recommendations"]]
            self.assertIn("durable state machine + deadline scheduling", patterns)
            self.assertTrue(recommendation["required_gates"] if "required_gates" in recommendation else recommendation["recommendations"])
            scheduler.close()
            memory.close()

    def test_runtime_feeds_beliefs_into_plan_and_learning_is_offloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured = {}
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: captured.update(context) or "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            project_key = runtime.session_store.project_key(root)
            cognitive = runtime.cognition(root)
            cognitive.hypotheses.propose(Hypothesis("h1", "q", "parser may fail", "test", confidence=0.2))
            result = runtime.run(
                session_id="s-belief",
                task_id="t-belief",
                project_root=root,
                intent="Fix parser",
                cognitive_capability="parser",
                cognitive_belief_evidence=(BeliefEvidence("be1", "h1", True, "fix succeeded", "runtime", 0.9),),
                learning_evidence=("be1",),
                learning_context="repo",
            )
            self.assertEqual(result.status.value, "accepted")
            self.assertEqual(captured["auren_cognitive_plan"]["belief_ids"], ["h1"])
            self.assertEqual(cognitive.hypotheses.assess("h1").confidence, 0.2)
            self.assertIsNotNone(runtime.last_deferred_learning_job)
            processed = runtime.process_learning(root)
            self.assertEqual(len(processed), 1)
            self.assertEqual(cognitive.hypotheses.assess("h1").confidence, 1.0)
            self.assertIsNone(runtime.last_learning_signal)
            scheduler.close()
            memory.close()

    def test_runtime_records_active_policy_strategy_when_no_explicit_strategy_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            policy = AdaptivePolicy(
                project=runtime.session_store.project_key(root), scope="global", version="v-deep",
                parent_version="v1", strategy="deep-verify", confidence_adjustment=0.0,
                iteration_target=2.0, created_at="2026-09-26T00:00:00+00:00", status="active", evidence_digest="evidence",
            )
            with patch("portable.adaptive_runtime.AdaptiveTuner.current_policy", return_value=policy):
                result = runtime.run(session_id="s-strategy", task_id="t-strategy", project_root=root, intent="use learned strategy")
            self.assertEqual(result.status.value, "accepted")
            records = __import__("portable.adaptive_tuning", fromlist=["AdaptiveTuner"]).AdaptiveTuner(memory, policy.project).history()
            self.assertEqual(records[-1].strategy, "deep-verify")
            scheduler.close()
            memory.close()

    def test_cognitive_persistence_failure_does_not_change_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            memory = FailingMemory(root / "memory.db", require_approval=False)
            runtime, _, scheduler = self._runtime(root, graph, memory=memory)
            result = runtime.run(session_id="s1", task_id="t2", project_root=root, intent="persist safely")
            self.assertEqual(result.status.value, "accepted")
            self.assertIsNotNone(runtime.last_cognitive_episode)
            self.assertEqual(runtime.last_cognitive_episode.status, "accepted")
            self.assertTrue(runtime.last_cognitive_episode.persistence_errors)
            self.assertIn("forced memory failure", runtime.last_cognitive_episode.persistence_errors[0])
            scheduler.close()


    def test_runtime_streams_canonical_memory_in_bounded_evidence_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            orchestrator = runtime.memory_orchestrator(root)
            saved = orchestrator.remember(
                "semantic", "preserve exact engineering evidence " * 90,
                source="test", approved=True, verified=True,
            )
            chunks = runtime.pack_memory_context_chunks(
                root, "preserve exact engineering evidence",
                required_memory_ids=(saved.memory_id,), chunk_chars=256,
                recall_budget_chars=10_000,
            )
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(len(chunk.text) <= 256 for chunk in chunks))
            self.assertEqual({saved.memory_id}, {rid for chunk in chunks for rid in chunk.record_ids})
            self.assertTrue(all(chunk.source_digest for chunk in chunks))
            scheduler.close()
            memory.close()

    def test_runtime_memory_chunking_fails_closed_when_recall_omits_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = Graph([Node("agent", NodeKind.AGENT, lambda context: "ok", critical=True, risk="low")])
            runtime, memory, scheduler = self._runtime(root, graph)
            orchestrator = runtime.memory_orchestrator(root)
            required = orchestrator.remember(
                "semantic", "bounded memory recall", source="test", approved=True, verified=True,
            )
            orchestrator.remember(
                "semantic", "bounded memory recall " + ("large payload " * 1000),
                source="test", approved=True, verified=False,
            )
            with self.assertRaisesRegex(ValueError, "recall omitted"):
                runtime.pack_memory_context_chunks(
                    root, "bounded memory recall",
                    required_memory_ids=(required.memory_id,), chunk_chars=256,
                    recall_budget_chars=100,
                )
            scheduler.close()
            memory.close()


if __name__ == "__main__":
    unittest.main()
