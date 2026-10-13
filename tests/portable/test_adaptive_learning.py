import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.adaptive_learning import AdaptiveLearningStore
from portable.persistent_memory import PersistentMemory


class AdaptiveLearningTests(unittest.TestCase):
    def test_outcome_is_deferred_until_explicit_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            store = AdaptiveLearningStore(memory, "project-x")
            job = store.record_outcome(
                task_id="t1",
                intent="fix parser",
                status="accepted",
                quality=0.9,
                iterations=2,
                context="concise_verified",
            )
            self.assertEqual(job.status, "pending")
            self.assertEqual(len(store.pending()), 1)
            profile_before = store.profile()
            self.assertEqual(profile_before.observations, 0)

    def test_processing_updates_workstyle_from_verified_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            store = AdaptiveLearningStore(memory, "project-x")
            store.record_outcome(task_id="t1", intent="fix", status="accepted", quality=0.9, iterations=1, context="concise")
            store.record_outcome(task_id="t2", intent="fix", status="accepted", quality=1.0, iterations=1, context="concise")
            processed = store.process(limit=10)
            self.assertEqual(len(processed), 2)
            profile = store.profile()
            self.assertEqual(profile.observations, 2)
            self.assertLessEqual(profile.iteration_target, 1.0)
            self.assertGreaterEqual(profile.confidence, 0.5)

    def test_processing_rejects_unverified_quality_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            store = AdaptiveLearningStore(memory, "project-x")
            store.record_outcome(task_id="t1", intent="fix", status="failed", quality=0.2, iterations=4, verified=False)
            processed = store.process(limit=10)
            self.assertEqual(len(processed), 1)
            self.assertEqual(store.profile().observations, 0)

    def test_profile_is_project_scoped_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.db"
            memory = PersistentMemory(path, require_approval=False)
            first = AdaptiveLearningStore(memory, "project-a")
            first.record_outcome(task_id="t1", intent="fix", status="accepted", quality=1.0, iterations=1, context="concise")
            first.process()
            second = AdaptiveLearningStore(memory, "project-b")
            self.assertEqual(second.profile().observations, 0)

    def test_processing_uses_repository_root_for_dream_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            memory = PersistentMemory(root / ".aer" / "memory.db", require_approval=False)
            store = AdaptiveLearningStore(memory, "project-x", dream_root=root)
            store.record_outcome(task_id="t1", intent="fix", status="accepted", quality=1.0, iterations=1, context="concise")
            with patch("portable.adaptive_learning.DreamMemory") as dream:
                store.process()
                dream.assert_called_once_with(root.resolve())


if __name__ == "__main__":
    unittest.main()
