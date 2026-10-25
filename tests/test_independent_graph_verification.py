from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator
from portable.independent_graph_verification import IndependentGraphVerifier


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=False, shell=False, timeout=20,
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "AUREN Test")
    git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "README.md", "src/base.py")
    git(root, "commit", "-qm", "initial")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class IndependentGraphVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        init_repo(self.repo)
        self.coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.root / "worker-workspaces"
        )
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        self.proposal = self.coordinator.prepare_proposal("worker-a")
        self.verifier = IndependentGraphVerifier(
            self.repo, verifier_identity="verifier-ci",
            trusted_verifier_ids=["verifier-ci"],
            worktree_root=self.root / "verifier-workspaces",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_verifier_runs_check_and_binds_receipt_to_artifact_and_intent(self):
        original = (self.repo / "src" / "base.py").read_text(encoding="utf-8")
        receipt = self.verifier.verify(
            self.proposal, worker_identity="worker-a",
            task_digest=sha("task-1"), intent_digest=sha("intent-1"),
            checks=[{
                "check_id": "feature-content",
                "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('src/feature.py').read_text() == 'answer = 42\\n'"],
                "timeout_seconds": 10,
            }],
        )
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["verification_mode"], "fresh-isolated-worktree")
        self.assertEqual(receipt["external_evaluator_independence"], "not_proven")
        self.assertEqual(receipt["source_revision"], self.proposal.base_revision)
        self.assertEqual(receipt["artifact_digest"], self.proposal.artifact_digest)
        self.assertEqual(receipt["task_digest"], sha("task-1"))
        self.assertEqual(receipt["intent_digest"], sha("intent-1"))
        self.assertEqual(receipt["checks"][0]["status"], "passed")
        self.assertEqual(receipt["cleanup_status"], "passed")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        self.assertEqual(receipt["receipt_digest"], hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest())
        self.assertEqual((self.repo / "src" / "base.py").read_text(encoding="utf-8"), original)
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.coordinator.cleanup_worker("worker-a")

    def test_failed_command_is_failed_not_worker_self_attested_success(self):
        receipt = self.verifier.verify(
            self.proposal, worker_identity="worker-a",
            task_digest=sha("task-1"), intent_digest=sha("intent-1"),
            checks=[{"check_id": "deliberate-failure", "argv": [sys.executable, "-c", "raise SystemExit(7)"]}],
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["checks"][0]["status"], "failed")
        self.assertEqual(receipt["checks"][0]["return_code"], 7)
        self.coordinator.cleanup_worker("worker-a")

    def test_worker_cannot_verify_own_proposal(self):
        with self.assertRaisesRegex(PermissionError, "cannot verify its own"):
            self.verifier.verify(
                self.proposal, worker_identity="verifier-ci",
                task_digest=sha("task-1"), intent_digest=sha("intent-1"),
                checks=[{"check_id": "smoke", "argv": [sys.executable, "-c", "pass"]}],
            )

    def test_untrusted_verifier_identity_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "trusted verifier allowlist"):
            IndependentGraphVerifier(
                self.repo, verifier_identity="untrusted",
                trusted_verifier_ids=["verifier-ci"], worktree_root=self.root / "other",
            )

    def test_missing_checks_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "checks are required"):
            self.verifier.verify(
                self.proposal, worker_identity="worker-a",
                task_digest=sha("task-1"), intent_digest=sha("intent-1"), checks=[],
            )

    def test_stale_source_revision_blocks_verification(self):
        (self.repo / "README.md").write_text("# advanced\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "advance source")
        with self.assertRaisesRegex(RuntimeError, "stale source revision"):
            self.verifier.verify(
                self.proposal, worker_identity="worker-a",
                task_digest=sha("task-1"), intent_digest=sha("intent-1"),
                checks=[{"check_id": "smoke", "argv": [sys.executable, "-c", "pass"]}],
            )
        self.coordinator.cleanup_worker("worker-a")

    def test_tampered_artifact_is_rejected_before_execution(self):
        Path(self.proposal.artifact_path).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "artifact digest mismatch"):
            self.verifier.verify(
                self.proposal, worker_identity="worker-a",
                task_digest=sha("task-1"), intent_digest=sha("intent-1"),
                checks=[{"check_id": "smoke", "argv": [sys.executable, "-c", "pass"]}],
            )
        self.coordinator.cleanup_worker("worker-a")

    def test_duplicate_check_ids_are_rejected(self):
        checks = [
            {"check_id": "same", "argv": [sys.executable, "-c", "pass"]},
            {"check_id": "same", "argv": [sys.executable, "-c", "pass"]},
        ]
        with self.assertRaisesRegex(ValueError, "ids must be unique"):
            self.verifier.verify(
                self.proposal, worker_identity="worker-a",
                task_digest=sha("task-1"), intent_digest=sha("intent-1"), checks=checks,
            )
        self.coordinator.cleanup_worker("worker-a")


if __name__ == "__main__":
    unittest.main()
