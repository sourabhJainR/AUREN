"""Unit tests for the production Docker external evaluator adapter."""
import json
import subprocess
import unittest
from unittest.mock import patch

from portable.docker_external_evaluator_executor import (
    ContainerIsolationError,
    DockerExternalEvaluatorExecutor,
)

_IMAGE = "sha256:" + "a" * 64


class DockerExternalEvaluatorExecutorTests(unittest.TestCase):
    def test_requires_linux_and_pinned_image(self):
        with patch("portable.docker_external_evaluator_executor.os.name", "nt"):
            with self.assertRaises(ContainerIsolationError):
                DockerExternalEvaluatorExecutor(image=_IMAGE)
        with self.assertRaises(ContainerIsolationError):
            DockerExternalEvaluatorExecutor(image="latest")

    def test_policy_probe_must_pass_every_required_control(self):
        executor = DockerExternalEvaluatorExecutor.__new__(DockerExternalEvaluatorExecutor)
        executor.runtime = "/usr/bin/docker"
        executor.image = _IMAGE
        executor.probe_timeout_seconds = 1
        executor.evidence = None
        executor.production_ready = False
        completed = subprocess.CompletedProcess([], 0, stdout="1.2.3", stderr="")
        inspect = subprocess.CompletedProcess([], 0, stdout=_IMAGE, stderr="")
        failed_probe = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "non_root": True, "root_read_only": True, "network_disabled": True,
                "tmpfs_hardened": True, "capabilities_dropped": True,
                "no_new_privileges": False,
            }),
            stderr="",
        )
        with patch("portable.docker_external_evaluator_executor.subprocess.run",
                   side_effect=[completed, inspect, failed_probe]):
            with self.assertRaisesRegex(ContainerIsolationError, "probes failed"):
                executor.verify_policy()
        self.assertFalse(executor.production_ready)

    @patch("portable.docker_external_evaluator_executor.subprocess.Popen")
    def test_launch_uses_dedicated_process_group_for_bounded_client_cleanup(self, popen):
        from portable.docker_external_evaluator_executor import _ContainerProcess
        process = type("FakeProcess", (), {
            "stdin": object(), "stdout": object(), "pid": 1234,
        })()
        popen.return_value = process
        with patch("portable.docker_external_evaluator_executor.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),  # docker kill
                subprocess.CompletedProcess([], 0),  # docker rm
            ]
            container = _ContainerProcess("/usr/bin/docker", "auren-test", ["/usr/bin/docker", "run"])
        self.assertEqual(container.pid, 1234)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])

    @patch("portable.docker_external_evaluator_executor.os.killpg")
    @patch("portable.docker_external_evaluator_executor.subprocess.run")
    def test_termination_reports_orphaned_container_and_preserves_failure(self, run, killpg):
        from portable.docker_external_evaluator_executor import _ContainerProcess
        import subprocess as subprocess_module
        process = type("FakeProcess", (), {
            "stdin": object(), "stdout": object(), "pid": 4321,
            "returncode": None,
            "wait": lambda self, timeout=None: setattr(self, "returncode", -9) or -9,
            "poll": lambda self: -9,
            "kill": lambda self: None,
        })()
        with patch("portable.docker_external_evaluator_executor.subprocess.Popen", return_value=process):
            container = _ContainerProcess("/usr/bin/docker", "auren-test", ["/usr/bin/docker", "run"])
        run.side_effect = [
            subprocess_module.CompletedProcess([], 1),  # kill failed
            subprocess_module.CompletedProcess([], 0, stdout="true\\n"),  # still running
            subprocess_module.CompletedProcess([], 1),  # rm failed
            subprocess_module.CompletedProcess([], 0),  # inspect says it still exists
        ]
        container.terminate_isolated()
        self.assertIn("termination could not be confirmed", container.termination_error)
        self.assertIn("still exists", container.termination_error)
        self.assertEqual(container.returncode, -9)

    def test_launch_fails_closed_without_fresh_policy_evidence(self):
        executor = DockerExternalEvaluatorExecutor.__new__(DockerExternalEvaluatorExecutor)
        executor.production_ready = False
        executor.evidence = None
        with self.assertRaisesRegex(ContainerIsolationError, "verify_policy"):
            executor.launch(type("Command", (), {"argv": ("python", "eval.py")})(), {})


if __name__ == "__main__":
    unittest.main()
