import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from portable.openshell_sandbox import (
    OpenShellExecutionRequest,
    OpenShellSandboxBackend,
    OpenShellUnavailableError,
)


class OpenShellSandboxBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "source.py").write_text("print('ok')", encoding="utf-8")
        self.policy = root / "policy.yaml"
        self.policy.write_text(
            "version: 1\nfilesystem_policy:\n  include_workdir: true\n"
            "  read_write: [/workspace, /tmp]\nnetwork_policies: []\n",
            encoding="utf-8",
        )
        self.request = OpenShellExecutionRequest(
            name_prefix="auren-test",
            workspace=str(self.workspace),
            policy_file=str(self.policy),
            command=("/bin/sh", "-lc", "cd /workspace && python -m pytest"),
            providers=("github-readonly",),
            timeout_seconds=15,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_executes_with_explicit_policy_upload_and_no_keep(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="tests passed", stderr="")
        backend = OpenShellSandboxBackend(runner=runner, which=lambda name: "/usr/bin/openshell")
        result = backend.execute(self.request)
        args, kwargs = calls[0]
        self.assertEqual(args[0:3], ["/usr/bin/openshell", "sandbox", "create"])
        self.assertIn("--policy", args)
        self.assertIn("--upload", args)
        self.assertIn("--no-keep", args)
        self.assertIn("--provider", args)
        self.assertEqual(kwargs["timeout"], 15.0)
        self.assertTrue(result.passed)
        self.assertEqual(result.policy_digest, __import__("hashlib").sha256(self.policy.read_bytes()).hexdigest())
        self.assertEqual(result.as_evidence()["stdout_digest"], __import__("hashlib").sha256(b"tests passed").hexdigest())
        self.assertNotIn("tests passed", repr(result.as_evidence()))

    def test_missing_cli_fails_closed_without_local_fallback(self):
        called = []
        backend = OpenShellSandboxBackend(runner=lambda *a, **k: called.append(True), which=lambda name: None)
        with self.assertRaises(OpenShellUnavailableError):
            backend.execute(self.request)
        self.assertEqual(called, [])

    def test_timeout_attempts_cleanup_and_returns_failed_receipt(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["sandbox", "create"]:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"partial", stderr=b"timed out")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        backend = OpenShellSandboxBackend(runner=runner, which=lambda name: "/usr/bin/openshell")
        result = backend.execute(self.request)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.cleanup_attempted)
        self.assertEqual(calls[1][1:3], ["sandbox", "delete"])
        self.assertFalse(result.passed)

    def test_invalid_request_is_rejected_before_launch(self):
        calls = []
        backend = OpenShellSandboxBackend(runner=lambda *a, **k: calls.append(True), which=lambda name: "/usr/bin/openshell")
        invalid = OpenShellExecutionRequest(
            name_prefix="BAD NAME", workspace=self.request.workspace,
            policy_file=self.request.policy_file, command=("python", "-m", "pytest"),
        )
        with self.assertRaisesRegex(ValueError, "name_prefix"):
            backend.execute(invalid)
        self.assertEqual(calls, [])

    def test_policy_is_required_and_must_be_nonempty(self):
        empty = Path(self.temp.name) / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        invalid = OpenShellExecutionRequest(
            name_prefix="auren-test", workspace=self.request.workspace,
            policy_file=str(empty), command=("echo", "hello"),
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            invalid.validate()



    def test_workspace_mutation_during_execution_fails_closed(self):
        def runner(args, **kwargs):
            (self.workspace / "unexpected.py").write_text("mutated", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="done", stderr="")
        backend = OpenShellSandboxBackend(runner=runner, which=lambda name: "/usr/bin/openshell")
        result = backend.execute(self.request)
        self.assertFalse(result.workspace_stable)
        self.assertFalse(result.passed)
        self.assertFalse(result.as_evidence()["workspace_stable"])

if __name__ == "__main__":
    unittest.main()
