import tempfile
import unittest
from pathlib import Path

from portable.learning_transfer import LearningTransfer
from portable.persistent_memory import PersistentMemory


class FailureDontTests(unittest.TestCase):
    def test_failure_is_retrievable_and_planning_can_use_it(self):
        with tempfile.TemporaryDirectory() as d:
            memory = PersistentMemory(Path(d) / "memory.sqlite3", require_approval=False)
            transfer = LearningTransfer(memory, "repo")
            item = transfer.record_failure(
                problem="implement API",
                dont="do not skip contract validation",
                evidence_ids=("e1",),
            )
            found = transfer.failure_constraints("implement API")
            self.assertEqual(item.dont, found[0].dont)
            self.assertEqual(found[0].evidence_ids, ("e1",))


if __name__ == "__main__":
    unittest.main()
