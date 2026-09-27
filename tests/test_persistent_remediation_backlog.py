from pathlib import Path
import tempfile
import unittest

from portable.persistent_memory import PersistentMemory
from portable.persistent_remediation_backlog import PersistentRemediationBacklog


class PersistentRemediationBacklogTests(unittest.TestCase):
    def test_survives_new_instances_and_reprioritizes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "memory.sqlite"
            first = PersistentRemediationBacklog(PersistentMemory(path, require_approval=False), "hws")
            first.upsert(
                finding_id="f1", task_family="review", capability="security",
                hat="security", severity="high", title="Unsafe path", detail="path escapes root",
                recommendation="reject unsafe path", evidence_ids=("e1",),
                session_id="s1", episode_id="e1",
            )
            second = PersistentRemediationBacklog(PersistentMemory(path, require_approval=False), "hws")
            self.assertEqual(second.resume_ids(), ("f1",))
            item = second.get("f1")
            self.assertEqual(item.last_session_id, "s1")
            self.assertEqual(item.last_episode_id, "e1")

    def test_status_resolution_removes_item_from_resume_queue(self):
        with tempfile.TemporaryDirectory() as td:
            memory = PersistentMemory(Path(td) / "memory.sqlite", require_approval=False)
            backlog = PersistentRemediationBacklog(memory, "hws")
            backlog.upsert(
                finding_id="f1", task_family="review", capability="quality",
                hat="quality", severity="medium", title="Missing test", detail="coverage gap",
                recommendation="add regression test", evidence_ids=("e1",),
            )
            backlog.set_status("f1", "resolved", evidence_ids=("verify-1",))
            self.assertEqual(backlog.resume_ids(), ())
            self.assertEqual(backlog.get("f1").resolution_evidence_ids, ("verify-1",))

    def test_learning_and_curriculum_adapters(self):
        class Learning:
            def __init__(self): self.calls = []
            def record_failure(self, **kwargs): self.calls.append(kwargs)
        class Curriculum:
            def __init__(self): self.calls = []
            def record_outcome(self, *args, **kwargs): self.calls.append((args, kwargs))
        with tempfile.TemporaryDirectory() as td:
            backlog = PersistentRemediationBacklog(PersistentMemory(Path(td) / "m.sqlite", require_approval=False), "hws")
            backlog.upsert(
                finding_id="f1", task_family="verification", capability="testing",
                hat="quality", severity="high", title="Weak regression", detail="missing regression",
                recommendation="add deterministic regression", evidence_ids=("e1",),
            )
            learning, curriculum = Learning(), Curriculum()
            self.assertEqual(backlog.feed_learning(learning), 1)
            self.assertEqual(backlog.feed_curriculum(curriculum), 1)
            self.assertEqual(learning.calls[0]["problem"], "missing regression")
            self.assertEqual(curriculum.calls[0][0], ("testing", "verification", "remediation"))
            self.assertEqual(curriculum.calls[0][1]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
