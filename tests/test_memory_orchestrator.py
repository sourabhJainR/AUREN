"""Behavioral coverage for unified memory routing, budgets and recovery."""
import tempfile
import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".ai-harness" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from context_cache import ContextPageCache
from portable.agent_capabilities import PersistentMemory
from portable.memory_orchestrator import MemoryBudgetError, MemoryOrchestrator, MEMORY_KINDS


class MemoryOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.db = PersistentMemory(self.root / "memory.sqlite3", require_approval=True)
        self.cache = ContextPageCache(page_chars=256)
        self.coordinator = MemoryOrchestrator(
            self.root, "project-a", memory=self.db, context_cache=self.cache,
            max_working_entries=3, max_working_chars=40,
            max_sensory_entries=3, sensory_ttl_seconds=0.03,
        )

    def test_supported_memory_kinds_have_explicit_owners(self):
        self.assertEqual({
            "episodic", "semantic", "working", "procedural", "sensory",
            "reflective", "hierarchical", "prospective", "meta_memory", "kv_cache",
        }, set(MEMORY_KINDS))

    def test_durable_memory_requires_approval_and_keeps_provenance(self):
        denied = self.coordinator.remember(
            "episodic", "build failed during integration test", source="test-run",
            evidence_ids=["log-42"], confidence=0.8,
        )
        self.assertEqual("approval_required", denied.status)
        stored = self.coordinator.remember(
            "episodic", "build failed during integration test", source="test-run",
            evidence_ids=["log-42"], confidence=0.8, approved=True,
        )
        self.assertEqual("stored", stored.status)
        node = self.coordinator.graph.get_node(stored.memory_id)
        self.assertEqual("test-run", node.source)
        self.assertEqual(["log-42"], node.properties["evidence_ids"])
        self.assertEqual("episodic", node.properties["memory_kind"])

    def test_recall_is_scoped_ranked_and_reuses_existing_cache(self):
        self.coordinator.remember(
            "semantic", "database migration uses a safe transaction", source="docs",
            confidence=0.7, approved=True,
        )
        first = self.coordinator.recall("database migration", kinds=("semantic",), budget_chars=200)
        second = self.coordinator.recall("database migration", kinds=("semantic",), budget_chars=200)
        self.assertIn("safe transaction", first.text)
        self.assertEqual(1, len(second.memories))
        self.assertGreaterEqual(second.cache_hits, 1)
        self.assertEqual("docs", second.memories[0].source)

    def test_working_memory_retains_multiple_entries_per_task_and_is_bounded(self):
        self.coordinator.remember("working", "first working note", source="agent", task_id="t1")
        self.coordinator.remember("working", "second working note", source="agent", task_id="t1")
        self.coordinator.remember("working", "third working note", source="agent", task_id="t2")
        recall = self.coordinator.recall(
            "working note", kinds=("working",), task_id="t1", budget_chars=200,
        )
        self.assertEqual(2, len(recall.memories))
        self.assertNotIn("third working note", recall.text)

    def test_sensory_memory_expires_without_background_polling(self):
        self.coordinator.remember("sensory", "temporary browser observation", source="browser", task_id="t1")
        self.assertEqual(1, len(self.coordinator.recall(
            "browser observation", kinds=("sensory",), task_id="t1", budget_chars=200,
        ).memories))
        time.sleep(0.05)
        self.assertEqual([], self.coordinator.recall(
            "browser observation", kinds=("sensory",), task_id="t1", budget_chars=200,
        ).memories)
        self.assertGreaterEqual(self.coordinator.metrics()["expired"], 1)

    def test_required_memory_fails_closed_when_budget_is_too_small(self):
        result = self.coordinator.remember(
            "semantic", "required acceptance contract memory", source="contract",
            approved=True, verified=True,
        )
        with self.assertRaises(MemoryBudgetError):
            self.coordinator.recall(
                "required acceptance contract", kinds=("semantic",),
                required_memory_ids=(result.memory_id,), budget_chars=8,
            )

    def test_owner_callback_is_reused_for_specialized_memory(self):
        calls = []
        def owner(**kwargs):
            calls.append(kwargs)
            return type("Receipt", (), {"id": "ledger-1"})()
        coordinator = MemoryOrchestrator(
            self.root, "project-b", memory=self.db, context_cache=self.cache,
            owner_callbacks={"procedural": owner},
        )
        result = coordinator.remember(
            "procedural", "verified test-first workflow", source="learning-ledger",
            task_id="t1", evidence_ids=["ci-1"], approved=True,
        )
        self.assertEqual("delegated", result.status)
        self.assertEqual("ledger-1", result.memory_id)
        self.assertEqual(("ci-1",), calls[0]["evidence_ids"])

    def test_kv_cache_and_meta_memory_do_not_create_second_durable_store(self):
        cached = self.coordinator.remember("kv_cache", "stable repository conventions", source="docs")
        meta = self.coordinator.remember("meta_memory", "do not persist metrics as memories", source="runtime")
        self.assertEqual("cached", cached.status)
        self.assertEqual("observed", meta.status)
        self.assertEqual(0, len(self.db.search("project-a", "stable repository conventions")))
        before = self.coordinator.metrics()["writes"]
        self.coordinator.metrics()
        self.assertEqual(before, self.coordinator.metrics()["writes"])

    def test_invalid_inputs_fail_early(self):
        with self.assertRaises(ValueError):
            self.coordinator.remember("unknown", "text", source="test")
        with self.assertRaises(ValueError):
            self.coordinator.recall("anything", budget_chars=0)
        with self.assertRaises(ValueError):
            self.coordinator.remember("sensory", "text", source="test", ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
