import tempfile
import unittest
from pathlib import Path

from portable.continuous_engineering_runtime import ContinuousEngineeringRuntime
from portable.persistent_memory import PersistentMemory


class ContinuousEngineeringRuntimeTests(unittest.TestCase):
    def test_episode_survives_restart_and_completed_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "memory.sqlite"
            m1 = PersistentMemory(path, require_approval=False)
            r1 = ContinuousEngineeringRuntime(m1, "p")
            first = r1.run(
                episode_id="e1", task_family="coding", capability="testing",
                execute=lambda plan: None,
                verify=lambda result: (True, ("verification-1",)),
            )
            self.assertTrue(first.state.state == "completed")
            m1.close()

            m2 = PersistentMemory(path, require_approval=False)
            r2 = ContinuousEngineeringRuntime(m2, "p")
            second = r2.run(
                episode_id="e1", task_family="coding", capability="testing",
                execute=lambda plan: (_ for _ in ()).throw(AssertionError("must not execute")),
                verify=lambda result: (True, ("verification-2",)),
            )
            self.assertTrue(second.resumed)
            self.assertEqual(second.state.state, "completed")
            self.assertIsNone(second.loop)

    def test_failed_episode_creates_durable_evolution_signal(self):
        with tempfile.TemporaryDirectory() as d:
            m = PersistentMemory(Path(d) / "m.sqlite", require_approval=False)
            runtime = ContinuousEngineeringRuntime(m, "p", evolution_threshold=2)
            # Seed two historical unresolved findings so execution is planned.
            runtime.control_plane.backlog.upsert(
                finding_id="f1", task_family="coding", capability="testing",
                hat="quality", severity="medium", title="repair", detail="x",
                recommendation="fix",
            )
            runtime.control_plane.backlog.upsert(
                finding_id="f2", task_family="coding", capability="testing",
                hat="quality", severity="medium", title="repair2", detail="x",
                recommendation="fix",
            )
            receipt = runtime.run(
                episode_id="e2", task_family="coding", capability="testing",
                execute=lambda plan: "result",
                verify=lambda result: (False, ("verification-failed",)),
            )
            self.assertEqual(receipt.state.state, "escalated")
            self.assertIsNotNone(receipt.evolution)

    def test_interrupted_execution_checkpoint_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            m = PersistentMemory(Path(d) / "m.sqlite", require_approval=False)
            runtime = ContinuousEngineeringRuntime(m, "p")
            runtime._save(
                episode_id="e3", task_family="coding", capability="testing",
                state="verifying", iteration=1, plan_digest="plan-x",
            )
            called = []
            receipt = runtime.run(
                episode_id="e3", task_family="coding", capability="testing",
                execute=lambda plan: called.append(1),
                verify=lambda result: (True, ("v",)),
            )
            self.assertEqual(receipt.state.state, "escalated")
            self.assertEqual(receipt.state.terminal_action, "manual_verification_required")
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
