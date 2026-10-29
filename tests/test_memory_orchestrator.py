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
from portable.context_engine import ContextEngine, ContextPolicy
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
            max_working_entries=3, max_working_chars=100,
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

    def test_legacy_memory_rows_migrate_as_unscoped_searchable_records(self):
        import sqlite3

        path = self.root / "legacy-memory.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.execute("""CREATE TABLE memory(
                id TEXT PRIMARY KEY, project TEXT NOT NULL, category TEXT NOT NULL,
                text TEXT NOT NULL, intent_digest TEXT, confidence REAL NOT NULL,
                verified INTEGER NOT NULL, created_at TEXT NOT NULL)""")
            db.execute(
                "INSERT INTO memory VALUES(?,?,?,?,?,?,?,?)",
                ("legacy-id", "project-a", "semantic", "legacy general memory",
                 None, 0.7, 1, "2026-01-01T00:00:00+00:00"),
            )
            db.commit()
        finally:
            db.close()

        migrated = PersistentMemory(path, require_approval=True)
        found = migrated.search("project-a", "legacy general memory")
        self.assertEqual(["legacy-id"], [record.id for record in found])
        self.assertIsNone(found[0].branch)
        self.assertIsNone(found[0].source_revision)

        retracted = migrated.retract(
            "project-a", "legacy-id", reason="legacy memory is no longer valid",
        )
        self.assertIsNotNone(retracted.retracted_at)
        self.assertEqual([], migrated.search("project-a", "legacy general memory"))

    def test_branch_revision_scope_and_retraction_survive_restart(self):
        revision_a = "a" * 40
        revision_b = "b" * 40
        shared = self.coordinator.remember(
            "semantic", "shared verified engineering contract", source="approved-policy",
            confidence=0.95, verified=True, approved=True,
        )
        saved = self.coordinator.remember(
            "episodic", "branch-specific recovery rule", source="commit-evidence",
            intent_digest="intent-graph-recovery", evidence_ids=("ci-run-1",),
            confidence=0.9, verified=True, approved=True,
            branch="feature/graph-recovery", source_revision=revision_a,
        )
        self.assertEqual("stored", saved.status)

        # An unscoped caller must not see branch- or revision-bound facts.
        unscoped = self.coordinator.recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", budget_chars=300,
        )
        self.assertNotIn(saved.memory_id, {item.memory_id for item in unscoped.memories})

        matching_branch = MemoryOrchestrator(
            self.root, "project-a", memory=self.db, context_cache=self.cache,
        )
        matched = matching_branch.recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", branch="feature/graph-recovery",
            source_revision=revision_a, budget_chars=300,
        )
        self.assertEqual([saved.memory_id], [item.memory_id for item in matched.memories])
        self.assertEqual("feature/graph-recovery", matched.memories[0].branch)
        self.assertEqual(revision_a, matched.memories[0].source_revision)
        shared_across_branch = matching_branch.recall(
            "shared verified engineering contract", kinds=("semantic",),
            branch="main", source_revision=revision_b, budget_chars=300,
        )
        self.assertEqual([shared.memory_id], [item.memory_id for item in shared_across_branch.memories])

        stale_revision = matching_branch.recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", branch="feature/graph-recovery",
            source_revision=revision_b, budget_chars=300,
        )
        other_branch = matching_branch.recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", branch="main",
            source_revision=revision_a, budget_chars=300,
        )
        other_project = MemoryOrchestrator(
            self.root, "project-b", memory=self.db, context_cache=self.cache,
        ).recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", branch="feature/graph-recovery",
            source_revision=revision_a, budget_chars=300,
        )
        self.assertEqual((), stale_revision.memories)
        self.assertEqual((), other_branch.memories)
        self.assertEqual((), other_project.memories)
        with self.assertRaisesRegex(KeyError, "not found in project scope"):
            self.db.retract("project-b", saved.memory_id, reason="cross-project retraction attempt")

        retracted = matching_branch.retract(
            saved.memory_id, reason="source revision superseded by reviewed change",
        )
        self.assertIsNotNone(retracted.retracted_at)
        self.assertEqual("source revision superseded by reviewed change", retracted.retraction_reason)
        node = matching_branch.graph.get_node(
            saved.memory_id, branch="feature/graph-recovery", source_revision=revision_a,
            include_retracted=True,
        )
        self.assertEqual(retracted.retracted_at, node.properties["retracted_at"])
        self.assertEqual(retracted.retraction_reason, node.properties["retraction_reason"])

        # Reopening the canonical SQLite store preserves the tombstone and its reason.
        reopened_store = PersistentMemory(self.root / "memory.sqlite3", require_approval=True)
        reopened = MemoryOrchestrator(
            self.root, "project-a", memory=reopened_store, context_cache=self.cache,
        )
        after_restart = reopened.recall(
            "branch-specific recovery rule", kinds=("episodic",),
            intent_digest="intent-graph-recovery", branch="feature/graph-recovery",
            source_revision=revision_a, budget_chars=300,
        )
        self.assertEqual((), after_restart.memories)
        repeated_retraction = reopened.retract(
            saved.memory_id, reason="later reason must not rewrite the audit record",
        )
        self.assertEqual(retracted.retracted_at, repeated_retraction.retracted_at)
        self.assertEqual(retracted.retraction_reason, repeated_retraction.retraction_reason)
        self.assertEqual(1, reopened.metrics()["retractions"])

    def test_canonical_context_engine_packs_required_memory_from_router(self):
        result = self.coordinator.remember(
            "semantic", "verified repository uses bounded state transitions", source="architecture",
            approved=True, verified=True,
        )
        engine = ContextEngine(ContextPolicy(max_chars=400, max_items=5, max_item_chars=300, output_chars=300))
        packed = engine.pack_from_memory(
            self.coordinator, "bounded state transitions", kinds=("semantic",),
            required_memory_ids=(result.memory_id,),
        )
        self.assertIn("verified repository uses bounded state transitions", packed)
        self.assertIn(result.memory_id, packed)

    def test_recall_uses_one_indexed_query_for_all_durable_memory_kinds(self):
        for kind in ("episodic", "semantic", "procedural"):
            self.coordinator.remember(
                kind, f"repository fact for {kind}", source="docs", approved=True,
            )
        before = self.coordinator.metrics()["durable_searches"]
        recall = self.coordinator.recall(
            "repository fact", kinds=("episodic", "semantic", "procedural"),
            budget_chars=400,
        )
        self.assertEqual(3, len(recall.memories))
        self.assertEqual(1, self.coordinator.metrics()["durable_searches"] - before)

    def test_working_memory_retains_multiple_entries_per_task_and_is_bounded(self):
        self.coordinator.remember("working", "first working note", source="agent", task_id="t1")
        self.coordinator.remember("working", "second working note", source="agent", task_id="t1")
        self.coordinator.remember("working", "third working note", source="agent", task_id="t2")
        recall = self.coordinator.recall(
            "working note", kinds=("working",), task_id="t1", budget_chars=200,
        )
        self.assertEqual(2, len(recall.memories))
        self.assertNotIn("third working note", recall.text)

    def test_working_memory_evicts_oldest_entry_at_entry_limit(self):
        for task_id in ("t1", "t2", "t3", "t4"):
            self.coordinator.remember(
                "working", f"note for {task_id}", source="agent", task_id=task_id,
            )
        self.assertEqual((), self.coordinator.recall(
            "note", kinds=("working",), task_id="t1", budget_chars=100,
        ).memories)
        self.assertEqual(3, self.coordinator.metrics()["working_entries"])
        self.assertGreaterEqual(self.coordinator.metrics()["evictions"], 1)

    def test_ephemeral_memory_rejects_oversized_entries_without_false_success(self):
        result = self.coordinator.remember(
            "working", "x" * 101, source="agent", task_id="oversized",
        )
        self.assertEqual("budget_exceeded", result.status)
        self.assertIsNone(result.memory_id)
        self.assertEqual(0, self.coordinator.metrics()["working_entries"])

    def test_sensory_memory_expires_without_background_polling(self):
        self.coordinator.remember("sensory", "temporary browser observation", source="browser", task_id="t1")
        self.assertEqual(1, len(self.coordinator.recall(
            "browser observation", kinds=("sensory",), task_id="t1", budget_chars=200,
        ).memories))
        time.sleep(0.05)
        self.assertEqual((), self.coordinator.recall(
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

    def test_hierarchical_memory_links_into_the_canonical_context_graph(self):
        parent = self.coordinator.remember(
            "semantic", "repository architecture", source="architecture-doc",
            approved=True, verified=True,
        )
        child = self.coordinator.remember(
            "hierarchical", "execution controller is a runtime component", source="architecture-doc",
            approved=True, parent_memory_id=parent.memory_id,
        )
        edge = self.coordinator.graph.neighbors(parent.memory_id, relation="contains")[0]
        self.assertEqual(child.memory_id, edge.target_id)
        recall = self.coordinator.recall(
            "execution controller", kinds=("hierarchical",), budget_chars=200,
        )
        items = self.coordinator.context_items(recall)
        self.assertEqual(child.memory_id, items[0].refs[0])
        self.assertEqual("hierarchical", items[0].kind)

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

    def test_kv_cache_recall_reads_existing_pages_without_creating_a_store(self):
        written = self.coordinator.remember(
            "kv_cache", "stable repository conventions and test-first workflow", source="docs",
            verified=True,
        )
        before = self.cache.stats()["pages"]
        recall = self.coordinator.recall(
            "repository conventions", kinds=("kv_cache",), budget_chars=200,
        )
        self.assertEqual(1, len(recall.memories))
        self.assertEqual(written.memory_id, recall.memories[0].memory_id)
        self.assertFalse(recall.memories[0].verified)
        self.assertIn("test-first workflow", recall.text)
        self.assertEqual(before, self.cache.stats()["pages"])
        self.assertGreaterEqual(recall.cache_hits, 1)

    def test_working_memory_respects_intent_scope(self):
        self.coordinator.remember(
            "working", "same task but first intent", source="agent",
            task_id="shared-task", intent_digest="intent-a",
        )
        self.coordinator.remember(
            "working", "same task but second intent", source="agent",
            task_id="shared-task", intent_digest="intent-b",
        )
        recall = self.coordinator.recall(
            "same task", kinds=("working",), task_id="shared-task",
            intent_digest="intent-a", budget_chars=200,
        )
        self.assertEqual(["same task but first intent"], [item.text for item in recall.memories])

    def test_kv_cache_and_meta_memory_do_not_create_second_durable_store(self):
        cached = self.coordinator.remember("kv_cache", "stable repository conventions", source="docs")
        meta = self.coordinator.remember("meta_memory", "do not persist metrics as memories", source="runtime")
        self.assertEqual("cached", cached.status)
        self.assertEqual("observed", meta.status)
        self.assertEqual(0, len(self.db.search("project-a", "stable repository conventions")))
        before = self.coordinator.metrics()["writes"]
        self.coordinator.metrics()
        self.assertEqual(before, self.coordinator.metrics()["writes"])

    def test_repeated_recall_and_restart_preserve_required_memory_under_fixed_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "durable-memory.sqlite3"
            store = PersistentMemory(db_path, require_approval=True)
            cache = ContextPageCache(page_chars=256)
            coordinator = MemoryOrchestrator(root, "soak-project", memory=store, context_cache=cache)
            saved = coordinator.remember(
                "semantic", "acceptance contract: preserve ownership, provenance, and rollback evidence",
                source="verified-contract", approved=True, verified=True,
            )
            for index in range(12):
                coordinator.remember(
                    "episodic", f"ownership provenance rollback optional detail {index}: " + ("optional detail " * 8),
                    source="test", approved=True, verified=index % 3 == 0,
                )

            for cycle in range(120):
                if cycle in {30, 60, 90}:
                    store.close()
                    store = PersistentMemory(db_path, require_approval=True)
                    cache = ContextPageCache(page_chars=256)
                    coordinator = MemoryOrchestrator(
                        root, "soak-project", memory=store, context_cache=cache,
                    )
                recall = coordinator.recall(
                    "ownership provenance rollback", kinds=("semantic", "episodic"),
                    budget_chars=420, required_memory_ids=(saved.memory_id,),
                )
                self.assertLessEqual(recall.chars_used, 420)
                self.assertIn(saved.memory_id, {item.memory_id for item in recall.memories})
                self.assertIn(
                    "preserve ownership, provenance, and rollback evidence", recall.text,
                )
                self.assertLessEqual(cache.stats()["pages"], 16)
            store.close()

    def test_task_aware_plan_disables_sampling_for_security_and_release_work(self):
        plan = self.coordinator.plan_for_task(
            "Explore options for security guardrail bypass recovery",
            allow_sampling=True, requires_full_coverage=False, budget_chars=900,
            required_memory_ids=("policy-1", "rollback-1"),
        )
        self.assertEqual("security", plan.task_class)
        self.assertEqual("high", plan.sensitivity)
        self.assertFalse(plan.sampling_allowed)
        self.assertEqual(("policy-1", "rollback-1"), plan.required_memory_ids)
        self.assertEqual(900, plan.budget_chars)
        self.assertIn("procedural", plan.kinds)

        release = self.coordinator.plan_for_task(
            "Deploy production release", allow_sampling=True, requires_full_coverage=False,
        )
        self.assertEqual("delivery", release.task_class)
        self.assertFalse(release.sampling_allowed)

    def test_task_aware_recall_emits_secret_free_receipt_and_preserves_required_context(self):
        saved = self.coordinator.remember(
            "semantic", "acceptance policy and rollback evidence must be retained",
            source="verified-contract", approved=True, verified=True,
        )
        result = self.coordinator.recall_for_task(
            "implement repository change with acceptance policy",
            required_memory_ids=(saved.memory_id,), budget_chars=500,
            allow_sampling=True, requires_full_coverage=False,
        )
        self.assertEqual("engineering", result.plan.task_class)
        self.assertFalse(result.plan.sampling_allowed)
        self.assertIn(saved.memory_id, result.receipt["selected_memory_ids"])
        self.assertEqual([saved.memory_id], result.receipt["required_memory_ids"])
        self.assertLessEqual(result.receipt["chars_used"], result.receipt["budget_chars"])
        self.assertNotIn("text", result.receipt)
        self.assertNotIn("source", result.receipt)
        self.assertNotIn("prompt", result.receipt)

    def test_task_aware_plan_rejects_invalid_budget_and_blank_intent(self):
        with self.assertRaises(ValueError):
            self.coordinator.plan_for_task(" ")
        with self.assertRaises(ValueError):
            self.coordinator.plan_for_task("code change", budget_chars=0)

    def test_invalid_inputs_fail_early(self):
        with self.assertRaises(ValueError):
            self.coordinator.remember("unknown", "text", source="test")
        with self.assertRaises(ValueError):
            self.coordinator.recall("anything", budget_chars=0)
        with self.assertRaises(ValueError):
            self.coordinator.remember("sensory", "text", source="test", ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
