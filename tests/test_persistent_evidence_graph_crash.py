from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.persistent_evidence_graph import PersistentEvidenceGraph
from portable.persistent_memory import PersistentMemory


def _crash_after_evidence_edge_commit(database: str, project: str) -> None:
    graph = PersistentEvidenceGraph(
        PersistentMemory(Path(database), require_approval=False), project
    )
    source = graph.add_node("source", "source-digest")
    target = graph.add_node("result", "result-digest")
    persist_edge = graph.add_edge

    def commit_then_die(left, relation, right):
        persist_edge(left, relation, right)
        os._exit(87)

    graph.add_edge = commit_then_die
    graph.add_edge(source, "produced", target)
    os._exit(88)


class PersistentEvidenceGraphCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "evidence.db"
        self.graph = PersistentEvidenceGraph(
            PersistentMemory(self.database, require_approval=False), "crash-recovery"
        )

    def test_explicit_metadata_conflict_fails_closed_and_legacy_format_is_compatible(self):
        original = self.graph.add_node("run", "digest-123", {"source": "trusted", "revision": "abc"})
        # Existing versions wrote JSON with default separators (spaces).
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE evidence_nodes SET metadata_json=? WHERE project=? AND node_id=?",
                (json.dumps(dict(original.metadata), sort_keys=True), self.graph.project, original.node_id),
            )
        self.assertEqual(original, self.graph.add_node("run", "digest-123"))
        self.assertEqual(
            original,
            self.graph.add_node("run", "digest-123", {"revision": "abc", "source": "trusted"}),
        )
        with self.assertRaisesRegex(ValueError, "metadata conflict"):
            self.graph.add_node("run", "digest-123", {"source": "untrusted", "revision": "abc"})
        self.assertEqual(original, self.graph.get_node(original.node_id))

    def test_process_death_after_edge_commit_replays_one_canonical_edge(self):
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_evidence_edge_commit,
            args=(str(self.database), self.graph.project),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("evidence edge crash fixture exceeded its time budget")
        self.assertEqual(87, process.exitcode)

        restarted = PersistentEvidenceGraph(
            PersistentMemory(self.database, require_approval=False), self.graph.project
        )
        source = restarted.add_node("source", "source-digest")
        target = restarted.add_node("result", "result-digest")
        recovered = restarted.add_edge(source, "produced", target)
        replayed = restarted.add_edge(source, "produced", target)
        self.assertEqual(recovered, replayed)
        self.assertEqual((recovered,), restarted.lineage(source.node_id, direction="out"))
        self.assertEqual((recovered,), restarted.lineage(target.node_id, direction="in"))


if __name__ == "__main__":
    unittest.main()
