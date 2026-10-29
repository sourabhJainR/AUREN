from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.agent_execution_guard import AgentAssignmentScopeDenied, agent_assignment_scope
from portable.subprocess_provider import SubprocessProviderInvoker


class AssignedSubprocessScopeTests(unittest.TestCase):
    def test_assigned_agent_is_denied_before_arbitrary_child_process_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            invoker = SubprocessProviderInvoker(
                [sys.executable, "-c", "print('must not launch')"],
                cwd=Path(directory),
            )
            with agent_assignment_scope(("allowed.txt",)):
                with patch("portable.subprocess_provider.subprocess.Popen") as popen:
                    with self.assertRaisesRegex(
                        AgentAssignmentScopeDenied,
                        "cannot prove enforcement of the exact delegated resource scope",
                    ):
                        invoker("run the assigned task", timeout_seconds=1)
                    popen.assert_not_called()

    def test_unassigned_scope_guard_is_noop(self):
        # The helper itself is a no-op when no assignment scope is active.
        from portable.agent_execution_guard import deny_unscoped_subprocess_for_assigned_agent

        deny_unscoped_subprocess_for_assigned_agent()


if __name__ == "__main__":
    unittest.main()
