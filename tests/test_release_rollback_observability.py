from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.agency_observability import Trace
from portable.agency_release_lifecycle import ArtifactStore


class ReleaseRollbackObservabilityTests(unittest.TestCase):
    def test_rollback_transition_is_recorded_in_rollback_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("known-good", encoding="utf-8")
            second.write_text("candidate", encoding="utf-8")
            store = ArtifactStore(root / "release")
            v1 = store.stage(first, "v1")
            v2 = store.stage(second, "v2")
            store.transition("promote", v1, "initial release",
                             verification_digest="verify-v1", review_digest="review-v1")
            store.transition("promote", v2, "candidate release",
                             verification_digest="verify-v2", review_digest="review-v2")

            trace = Trace("rollback-run", run_id="rollback-run", task_id="release-task")
            parent = trace.span("supervisor", phase="supervisor")
            state = store.transition("rollback", None, "candidate regression",
                                     trace=trace, parent_span_id=parent.span_id)

            self.assertEqual("rollback", state.action)
            transition = next(span for span in trace.spans if span.name == "release_transition:rollback")
            self.assertEqual("rollback", transition.phase)
            self.assertEqual(parent.span_id, transition.parent_id)
            self.assertEqual("ok", transition.status)
            self.assertIsNotNone(transition.end)


if __name__ == "__main__":
    unittest.main()
