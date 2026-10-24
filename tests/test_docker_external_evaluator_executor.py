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

    def test_launch_fails_closed_without_fresh_policy_evidence(self):
        executor = DockerExternalEvaluatorExecutor.__new__(DockerExternalEvaluatorExecutor)
        executor.production_ready = False
        executor.evidence = None
        with self.assertRaisesRegex(ContainerIsolationError, "verify_policy"):
            executor.launch(type("Command", (), {"argv": ("python", "eval.py")})(), {})


if __name__ == "__main__":
    unittest.main()
