import tempfile
import unittest
from pathlib import Path

from portable.agent_capabilities import PersistentMemory
from portable.context_graph import ContextGraph, ContextNode


class ContextGraphTests(unittest.TestCase):
    def test_node_and_edge_round_trip_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite"
            memory = PersistentMemory(path, require_approval=False)
            graph = ContextGraph(memory, "project")
            graph.upsert_node(ContextNode("task:1", "task", "Fix timeout", "test", 0.9))
            graph.upsert_node(ContextNode("file:1", "file", "client.py", "repo", 1.0))
            graph.link("task:1", "touches", "file:1", source="test", confidence=0.8, properties={"line": 42})
            self.assertEqual(graph.get_node("task:1").label, "Fix timeout")
            self.assertEqual(graph.neighbors("task:1")[0].target_id, "file:1")
            self.assertEqual(graph.neighbors("file:1", direction="in")[0].source_id, "task:1")
            self.assertEqual(graph.neighbors("task:1", relation="touches")[0].properties, {"line": 42})
            digest = graph.digest()
            memory.close()

            reopened = PersistentMemory(path, require_approval=False)
            graph2 = ContextGraph(reopened, "project")
            self.assertEqual(graph2.digest(), digest)
            self.assertTrue(graph2.get_node("task:1").created_at)
            reopened.close()

    def test_duplicate_writes_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            graph = ContextGraph(memory, "project")
            node = ContextNode("task:1", "task", "Fix timeout", "test", 0.9, {"priority": 1})
            self.assertEqual(graph.upsert_node(node), node)
            self.assertEqual(graph.upsert_node(node), node)
            updated = graph.upsert_node(ContextNode("task:1", "task", "Fix timeout now", "test", 0.95, {"priority": 2}))
            self.assertEqual(updated.created_at, node.created_at)
            self.assertEqual(graph.get_node("task:1").label, "Fix timeout now")
            memory.close()

    def test_neighbors_are_deterministic_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            graph = ContextGraph(memory, "project", max_nodes=4, max_edges=2)
            graph.upsert_node(ContextNode("a", "task", "A", "test"))
            for node_id in ("c", "b", "d"):
                graph.upsert_node(ContextNode(node_id, "file", node_id, "test"))
            for node_id in ("c", "b"):
                graph.link("a", "touches", node_id, source="test")
            with self.assertRaisesRegex(ValueError, "edge budget"):
                graph.link("a", "touches", "d", source="test")
            self.assertEqual([edge.target_id for edge in graph.neighbors("a", limit=2)], ["b", "c"])
            with self.assertRaisesRegex(ValueError, "direction"):
                graph.neighbors("a", direction="sideways")
            memory.close()

    def test_neighbors_respect_branch_and_source_revision_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            graph = ContextGraph(memory, "project")
            revision_a = "a" * 40
            revision_b = "b" * 40
            graph.upsert_node(ContextNode("global", "task", "Global task", "test"))
            graph.upsert_node(ContextNode(
                "branch-a", "semantic", "Branch A lesson", "memory",
                properties={"branch": "feature/a", "source_revision": revision_a},
            ))
            graph.upsert_node(ContextNode(
                "branch-b", "semantic", "Branch B lesson", "memory",
                properties={"branch": "feature/b", "source_revision": revision_b},
            ))
            graph.link("global", "uses", "branch-a", source="test")
            graph.link("global", "uses", "branch-b", source="test")

            self.assertEqual((), graph.neighbors("global"))
            self.assertEqual(
                ["branch-a"],
                [edge.target_id for edge in graph.neighbors(
                    "global", branch="feature/a", source_revision=revision_a,
                )],
            )
            self.assertEqual((), graph.neighbors(
                "global", branch="feature/a", source_revision=revision_b,
            ))
            with self.assertRaisesRegex(PermissionError, "across branch scopes"):
                graph.link("branch-a", "related_to", "branch-b", source="test")
            with self.assertRaisesRegex(PermissionError, "across source_revision scopes"):
                graph.link("branch-a", "related_to", "global", source="test")
            memory.close()

    def test_retracted_nodes_are_hidden_and_cannot_be_revived(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            graph = ContextGraph(memory, "project")
            graph.upsert_node(ContextNode("task", "task", "Task", "test"))
            graph.upsert_node(ContextNode("lesson", "semantic", "Old lesson", "memory"))
            graph.link("task", "uses", "lesson", source="test")

            graph.upsert_node(ContextNode(
                "lesson", "semantic", "Old lesson", "memory",
                properties={"retracted_at": "2026-10-27T00:00:00+00:00",
                            "retraction_reason": "superseded"},
            ))
            self.assertEqual((), graph.neighbors("task"))
            self.assertEqual(1, len(graph.neighbors("task", include_retracted=True)))
            with self.assertRaisesRegex(PermissionError, "cannot link a retracted context node"):
                graph.link("task", "uses", "lesson", source="test")
            with self.assertRaisesRegex(PermissionError, "cannot be revived"):
                graph.upsert_node(ContextNode("lesson", "semantic", "Old lesson", "memory"))
            with self.assertRaisesRegex(PermissionError, "audit is immutable"):
                graph.upsert_node(ContextNode(
                    "lesson", "semantic", "Old lesson", "memory",
                    properties={"retracted_at": "2026-10-28T00:00:00+00:00",
                                "retraction_reason": "rewritten"},
                ))
            memory.close()

    def test_digest_is_stable_for_same_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.sqlite", require_approval=False)
            graph = ContextGraph(memory, "project")
            graph.upsert_node(ContextNode("a", "task", "A", "test", 1.0))
            graph.upsert_node(ContextNode("b", "file", "B", "test", 1.0))
            graph.link("a", "touches", "b", source="test", confidence=0.7)
            self.assertEqual(graph.digest(), graph.digest())
            memory.close()


if __name__ == "__main__":
    unittest.main()
