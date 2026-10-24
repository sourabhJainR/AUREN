from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from portable.docker_isolated_evaluator import DockerEvaluatorPolicy, DockerIsolatedEvaluatorExecutor, _DockerAttachedProcess


IMAGE = "registry.example.org/auren/evaluator@sha256:" + "a" * 64


@unittest.skipUnless(os.name == "posix" and Path("/proc").is_dir(), "Docker isolation adapter is Linux-only")
class DockerEvaluatorPolicyTests(unittest.TestCase):
    def test_requires_digest_pinned_image(self):
        with self.assertRaisesRegex(ValueError, "pinned by an immutable sha256 digest"):
            DockerEvaluatorPolicy("registry.example.org/auren/evaluator:latest")

    def test_rejects_root_and_unbounded_resource_policy(self):
        with self.assertRaisesRegex(ValueError, "non-root"):
            DockerEvaluatorPolicy(IMAGE, uid=0)
        with self.assertRaisesRegex(ValueError, "pids_limit"):
            DockerEvaluatorPolicy(IMAGE, pids_limit=0)
        with self.assertRaisesRegex(ValueError, "max_input_bytes"):
            DockerEvaluatorPolicy(IMAGE, max_input_bytes=0)

    @patch("portable.docker_isolated_evaluator.subprocess.run")
    def test_readiness_requires_linux_daemon_and_exact_pinned_image(self, run):
        run.side_effect = [
            Mock(returncode=0, stdout="linux\n"),
            Mock(returncode=0, stdout=IMAGE + "\n"),
        ]
        executor = DockerIsolatedEvaluatorExecutor(DockerEvaluatorPolicy(IMAGE))
        self.assertTrue(executor.production_ready)
        self.assertEqual(run.call_count, 2)
        self.assertIn("image", run.call_args_list[1].args[0])

    @patch("portable.docker_isolated_evaluator.subprocess.Popen")
    @patch("portable.docker_isolated_evaluator.subprocess.run")
    def test_launch_enforces_container_security_contract(self, run, popen):
        run.side_effect = [
            Mock(returncode=0, stdout="linux\n"),
            Mock(returncode=0, stdout=IMAGE + "\n"),
            Mock(returncode=0),
            Mock(returncode=0),
        ]
        process = Mock()
        process.stdin = Mock()
        process.stdout = Mock()
        process.pid = 1234
        popen.return_value = process
        executor = DockerIsolatedEvaluatorExecutor(DockerEvaluatorPolicy(IMAGE))
        attached = executor.launch(Mock(argv=("python", "-I", "/opt/evaluator.py")), {"PATH": "/host/path", "LANG": "C"})
        args = popen.call_args.args[0]
        for required in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", "--memory", "--cpus", "--tmpfs"):
            self.assertIn(required, args)
        self.assertIn("--user", args)
        self.assertIn("65532:65532", args)
        self.assertIn(IMAGE, args)
        self.assertNotIn("/host/path", args)
        self.assertEqual(attached.pid, 1234)
        cid_path = Path(args[args.index("--cidfile") + 1])
        cid_path.write_text("container-id", encoding="ascii")
        attached.terminate_isolated()
        self.assertTrue(any(call.args[0][1] == "kill" for call in run.call_args_list if call.args))


    @patch("portable.docker_isolated_evaluator.os.killpg")
    @patch("portable.docker_isolated_evaluator.subprocess.run")
    def test_cleanup_failure_is_recorded_and_container_evidence_is_preserved(self, run, killpg):
        with tempfile.TemporaryDirectory() as directory:
            cid_path = Path(directory) / "container.cid"
            cid_path.write_text("container-id", encoding="ascii")
            process = Mock()
            process.stdin = Mock()
            process.stdout = Mock()
            process.pid = 4321
            process.wait.return_value = -9
            run.side_effect = [
                Mock(returncode=1),                       # docker kill failed
                Mock(returncode=0, stdout="true\\n"), # container still running
                Mock(returncode=1),                       # docker rm failed
                Mock(returncode=0),                       # container still exists
            ]
            attached = _DockerAttachedProcess(process, cid_path, "docker")
            attached.terminate_isolated()
            self.assertIn("remained running", attached.termination_error)
            self.assertIn("still exists", attached.termination_error)
            self.assertTrue(cid_path.exists(), "retain the container ID for reconciliation")


if __name__ == "__main__":
    unittest.main()
