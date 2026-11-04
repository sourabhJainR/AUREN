"""Host composition regressions for continuous execution."""

from __future__ import annotations

import unittest

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind


def _runtime_probe(state):
    return {"ready": True}


class AdaptiveRuntimeContinuousSupervisorTests(unittest.TestCase):
    def test_continuous_supervisor_reuses_canonical_controller_and_recovery_ledger(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            runtime = AdaptiveRuntime(
                Graph([Node("probe", NodeKind.DETERMINISTIC, _runtime_probe)]),
                state_root=Path(tmp),
            )
            supervisor = runtime.create_continuous_supervisor()
            self.assertIs(supervisor.controller, runtime.execution_controller)
            self.assertIs(supervisor.recovery_ledger, runtime.recovery_ledger)
            self.assertEqual("stopped", supervisor.status()["state"])

    def test_parent_goal_reconcilers_require_trusted_plan_dispatch_root(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            runtime = AdaptiveRuntime(
                Graph([Node("probe", NodeKind.DETERMINISTIC, _runtime_probe)]),
                state_root=Path(tmp),
            )
            with self.assertRaisesRegex(RuntimeError, "trusted plan dispatch root"):
                runtime.create_continuous_supervisor(
                    goal_reconcilers={
                        "AUREN:test:untrusted-goal": lambda: {"status": "in_progress"},
                    },
                )


if __name__ == "__main__":
    unittest.main()
