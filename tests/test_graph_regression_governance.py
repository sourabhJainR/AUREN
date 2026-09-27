import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from portable.persistent_memory import PersistentMemory
from portable.regression_corpus import RegressionCorpus
from portable.agency_state_graph import ConvergenceGuard, NodeContract, StateGraph


class RegressionCorpusTests(unittest.TestCase):
    def make_memory(self):
        return PersistentMemory(Path(tempfile.mkdtemp()) / "memory.db")

    def episode(self, episode_id="ep-1", task_id="task-1", failure_class="", rules=()):
        return SimpleNamespace(
            phase=SimpleNamespace(value="failed" if failure_class else "completed"),
            evidence_ids=("e1", "e2"),
            episode_id=episode_id,
            task_id=task_id,
            intent_digest="intent-1",
            capability="engineering",
            failure_class=failure_class,
            dont_rules=tuple(rules),
        )

    def test_episode_creates_candidate_and_repeated_independent_passes_activate(self):
        memory = self.make_memory()
        corpus = RegressionCorpus(memory, "p")
        case = corpus.ingest_episode(self.episode())
        self.assertEqual(case.status, "candidate")
        case = corpus.record_validation(case.case_id, passed=True, evidence_ids=("v1",))
        self.assertEqual(case.status, "candidate")
        case = corpus.record_validation(case.case_id, passed=True, evidence_ids=("v2",))
        self.assertEqual(case.status, "active")
        self.assertEqual(len(corpus.active()), 1)

    def test_failed_validation_demotes_active_case(self):
        memory = self.make_memory()
        corpus = RegressionCorpus(memory, "p")
        case = corpus.ingest_episode(self.episode())
        corpus.record_validation(case.case_id, passed=True, evidence_ids=("v1",))
        corpus.record_validation(case.case_id, passed=True, evidence_ids=("v2",))
        case = corpus.record_validation(case.case_id, passed=False, evidence_ids=("v3",))
        self.assertEqual(case.status, "candidate")
        self.assertEqual(case.consecutive_passes, 0)

    def test_matching_episode_strengthens_one_case(self):
        memory = self.make_memory()
        corpus = RegressionCorpus(memory, "p")
        first = corpus.ingest_episode(self.episode("ep-1", task_id="task-a"))
        second = corpus.ingest_episode(self.episode("ep-2", task_id="task-b"))
        self.assertEqual(first.case_id, second.case_id)
        self.assertEqual(len(second.evidence_ids), 2)


class GraphConstructTests(unittest.TestCase):
    def test_edge_contract_rejects_undeclared_data(self):
        graph = StateGraph()
        graph.add_node("a", lambda _: {"x": 1}, contract=NodeContract(outputs=("x",)))
        graph.add_node("b", lambda s: {"y": s["x"]}, contract=NodeContract(inputs=("x",), outputs=("y",)))
        with self.assertRaises(ValueError):
            graph.add_edge("a", "b", data_keys=("missing",))

    def test_parallel_failure_can_be_isolated(self):
        graph = StateGraph()
        graph.add_node("ok", lambda _: {"ok": True})
        graph.add_node("bad", lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        graph.add_edge(graph.START, "ok")
        graph.add_edge(graph.START, "bad")
        graph.add_edge("ok", graph.END)
        graph.add_edge("bad", graph.END)
        run = graph.compile().invoke({}, parallel_nodes=lambda _: True, max_parallel_nodes=2,
                                    parallel_failure_mode="isolate")
        self.assertTrue(run.state["ok"])
        self.assertTrue(any(e.status == "failed-isolated" for e in run.events))

    def test_convergence_dedupes_rejected_and_confirmed_items(self):
        rounds = (({"id": 1}, {"id": 2}), ({"id": 2},), ({"id": 3},), ({"id": 3},))
        result = ConvergenceGuard(max_dry_rounds=2).unique_rounds(rounds)
        self.assertEqual(result, (({"id": 1}, {"id": 2}), ({"id": 3},)))


if __name__ == "__main__":
    unittest.main()
