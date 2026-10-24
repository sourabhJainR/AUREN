"""Real container-engine smoke test; enabled by CI after building its pinned image."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from portable.container_sandboxed_arena import run_campaign_container_sandboxed
from portable.independent_arena import NonceLedger, seal_manifest, verify_receipt


class ContainerSandboxedArenaIntegrationTests(unittest.TestCase):
    def test_real_docker_execution_with_local_content_addressed_image(self):
        image = os.environ.get("AUREN_ARENA_TEST_IMAGE", "")
        if not image or not shutil.which("docker"):
            self.skipTest("CI must build the fixture image and set AUREN_ARENA_TEST_IMAGE")
        if not image.startswith("sha256:"):
            self.fail("integration image must be an immutable local sha256 image ID")

        cases = [
            ("math-001", "What is 7 + 8?", 15),
            ("isolation-uid", "__probe__:unprivileged", True),
            ("isolation-readonly", "__probe__:root-read-only", True),
            ("isolation-tmpfs", "__probe__:tmpfs-hardening", True),
            ("isolation-network", "__probe__:network-disabled", True),
        ]
        sealed = seal_manifest({
            "benchmark_id": "arena-container-isolation-v1",
            "evaluator_id": "evaluator-parent",
            "runtime_id": "container-policy",
            "cases": [
                {"case_id": case_id, "domain": "container-isolation",
                 "split": "sealed_holdout", "prompt": prompt, "oracle": oracle}
                for case_id, prompt, oracle in cases
            ],
        })
        with tempfile.TemporaryDirectory() as directory:
            receipt = run_campaign_container_sandboxed(
                sealed,
                container_image=image,
                policy_digest=image,
                run_nonce="docker-isolation-probe-run-001",
                nonce_ledger=NonceLedger(Path(directory) / "replay.sqlite3"),
                container_runtime=shutil.which("docker") or "docker",
                timeout_seconds=20,
            )
        self.assertEqual(receipt["counts"], {"pass": len(cases), "fail": 0, "error": 0})
        self.assertTrue(verify_receipt(receipt, sealed))


if __name__ == "__main__":
    unittest.main()
