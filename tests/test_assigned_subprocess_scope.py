from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AI_HARNESS = ROOT / ".ai-harness"
if str(AI_HARNESS) not in sys.path:
    sys.path.insert(0, str(AI_HARNESS))

import provider as harness_provider
import resilient_provider
from portable.agent_execution_guard import AgentAssignmentScopeDenied, agent_assignment_scope
from portable.local_offload import LocalOffloadBroker, OffloadJob
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

    def test_local_offload_direct_launch_is_rejected_before_popen(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = LocalOffloadBroker(
                directory, allowed_programs={Path(sys.executable).name.lower()}
            )
            job = OffloadJob(
                "assigned-offload",
                (sys.executable, "-c", "print('must not launch')"),
                cwd=directory,
            )
            with agent_assignment_scope(("allowed.txt",)):
                with patch("portable.local_offload.subprocess.Popen") as popen:
                    result = broker.run(job)
                    self.assertEqual(result.status, "rejected")
                    self.assertIn("assigned agent subprocess launch is blocked", result.error or "")
                    popen.assert_not_called()

    def test_local_offload_batch_checks_scope_before_thread_pool_context_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = LocalOffloadBroker(
                directory, allowed_programs={Path(sys.executable).name.lower()}
            )
            jobs = [
                OffloadJob(
                    f"assigned-batch-{index}",
                    (sys.executable, "-c", "print('must not launch')"),
                    cwd=directory,
                )
                for index in range(2)
            ]
            with agent_assignment_scope(("allowed.txt",)):
                with patch("portable.local_offload.subprocess.Popen") as popen:
                    results = broker.run_many(jobs)
                    self.assertEqual([result.status for result in results], ["rejected", "rejected"])
                    self.assertTrue(all("assigned agent subprocess launch is blocked" in (result.error or "") for result in results))
                    popen.assert_not_called()

    def _assert_provider_launcher_denied(self, module, prompt_file: Path) -> None:
        argv = [
            str(module.__file__), "--prompt-file", str(prompt_file), "--",
            sys.executable, "-c", "print('must not launch')",
        ]
        stderr = io.StringIO()
        with agent_assignment_scope(("allowed.txt",)):
            with patch.object(sys, "argv", argv), patch.object(module.subprocess, "Popen") as popen:
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(module.main(), 78)
                popen.assert_not_called()
        self.assertIn("assigned agent subprocess launch is blocked", stderr.getvalue())

    def test_resilient_provider_denies_before_prompt_read_or_popen(self):
        with tempfile.TemporaryDirectory() as directory:
            self._assert_provider_launcher_denied(
                resilient_provider, Path(directory) / "missing-prompt.md"
            )

    def test_provider_bridge_denies_before_prompt_validation_or_popen(self):
        with tempfile.TemporaryDirectory() as directory:
            self._assert_provider_launcher_denied(
                harness_provider, Path(directory) / "missing-prompt.md"
            )


if __name__ == "__main__":
    unittest.main()
