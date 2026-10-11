"""Security regressions for the container-backed Arena runner."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.container_sandboxed_arena import run_campaign_container_sandboxed
from portable.independent_arena import NonceLedger, seal_manifest, verify_receipt

IMAGE_DIGEST = "a" * 64
IMAGE = "example.invalid/auren-policy@sha256:" + IMAGE_DIGEST
POLICY_DIGEST = "sha256:" + IMAGE_DIGEST


def fixture():
    return seal_manifest({
        "benchmark_id": "container-mini-v1",
        "evaluator_id": "evaluator-parent",
        "runtime_id": "container-policy",
        "cases": [
            {"case_id": "case-1", "domain": "arithmetic", "split": "sealed_holdout",
             "prompt": "What is 7 + 8?", "oracle": 15},
        ],
    })


class ContainerSandboxedArenaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = NonceLedger(Path(self.temp.name) / "nonces.sqlite3")

    def run_campaign(self, nonce="run-1", **kwargs):
        return run_campaign_container_sandboxed(
            fixture(),
            container_image=IMAGE,
            policy_digest=POLICY_DIGEST,
            run_nonce=nonce,
            nonce_ledger=self.ledger,
            **kwargs,
        )

    @patch("portable.container_sandboxed_arena.subprocess.run")
    def test_container_is_networkless_read_only_unprivileged_and_resource_limited(self, run):
        run.return_value = subprocess.CompletedProcess(["docker"], 0, stdout="15\n", stderr="")
        receipt = self.run_campaign()
        self.assertEqual(receipt["counts"], {"pass": 1, "fail": 0, "error": 0})
        self.assertTrue(verify_receipt(receipt, fixture()))
        command = run.call_args.args[0]
        for flag in (
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--memory-swap=256m", "--cpus=1",
            "--user=65534:65534", "--pull=never",
        ):
            self.assertIn(flag, command)
        self.assertFalse(any(arg == "-v" or arg == "--volume" or arg.startswith("--mount") for arg in command))
        self.assertIn("--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m", command)
        sent_task = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(set(sent_task), {"case_id", "domain", "prompt"})
        self.assertNotIn("oracle", sent_task)
        self.assertNotIn("split", sent_task)
        self.assertFalse(run.call_args.kwargs["shell"])

    @patch("portable.container_sandboxed_arena.subprocess.run")
    def test_timeout_requests_forced_container_cleanup(self, run):
        run.side_effect = [
            subprocess.TimeoutExpired("docker run", 0.01),
            subprocess.CompletedProcess(["docker", "rm"], 0, stdout="", stderr=""),
        ]
        receipt = self.run_campaign(timeout_seconds=0.01)
        self.assertEqual(receipt["counts"], {"pass": 0, "fail": 0, "error": 1})
        cleanup = run.call_args_list[1].args[0]
        self.assertEqual(cleanup[:3], ["docker", "rm", "--force"])
        self.assertTrue(cleanup[3].startswith("auren-arena-"))

    @patch("portable.container_sandboxed_arena.subprocess.run")
    def test_nonzero_policy_exit_is_not_a_pass(self, run):
        run.return_value = subprocess.CompletedProcess(["docker"], 2, stdout="15", stderr="secret")
        receipt = self.run_campaign()
        self.assertEqual(receipt["counts"], {"pass": 0, "fail": 0, "error": 1})
        self.assertNotIn("secret", json.dumps(receipt))

    @patch("portable.container_sandboxed_arena.subprocess.run")
    def test_rejects_mutable_image_tag_and_mismatched_policy_digest_before_execution(self, run):
        with self.assertRaisesRegex(ValueError, "immutable"):
            run_campaign_container_sandboxed(
                fixture(), container_image="example.invalid/policy:latest",
                policy_digest=POLICY_DIGEST, run_nonce="bad-image", nonce_ledger=self.ledger,
            )
        with self.assertRaisesRegex(ValueError, "policy_digest"):
            run_campaign_container_sandboxed(
                fixture(), container_image=IMAGE, policy_digest="sha256:" + "b" * 64,
                run_nonce="bad-digest", nonce_ledger=self.ledger,
            )
        run.assert_not_called()

    @patch("portable.container_sandboxed_arena.subprocess.run")
    def test_accepts_windows_executable_path_with_spaces(self, run):
        run.return_value = subprocess.CompletedProcess(["docker"], 0, stdout="15\n", stderr="")
        self.run_campaign(container_runtime=r"C:\Program Files\Docker\docker.exe")
        self.assertEqual(run.call_args.args[0][0], r"C:\\Program Files\\Docker\\docker.exe")

    def test_rejects_shell_command_as_container_runtime(self):
        with self.assertRaisesRegex(ValueError, "executable"):
            self.run_campaign(container_runtime="docker; echo unsafe")


if __name__ == "__main__":
    unittest.main()
