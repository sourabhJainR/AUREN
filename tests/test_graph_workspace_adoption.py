from __future__ import annotations

import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=False, shell=False, timeout=20,
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "AUREN Test")
    git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "README.md", "src/base.py")
    git(root, "commit", "-qm", "initial")
    return git(root, "rev-parse", "HEAD")


class GraphWorkspaceAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.base = init_repo(self.repo)
        self.coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.root / "isolated-workspaces"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_worker_gets_separate_worktree_and_source_is_unchanged(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        self.assertNotEqual(workspace.resolve(), self.repo.resolve())
        self.assertEqual(git(workspace, "rev-parse", "HEAD"), self.base)
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        proposal = self.coordinator.prepare_proposal("worker-a")
        self.assertEqual(proposal.paths, ("src/feature.py",))
        self.assertTrue(Path(proposal.artifact_path).is_file())

    def test_overlapping_file_ownership_is_rejected_before_second_worktree(self):
        self.coordinator.create_worker("worker-a", ["src"])
        with self.assertRaisesRegex(PermissionError, "overlaps"):
            self.coordinator.create_worker("worker-b", ["src/feature.py"])
        self.assertNotIn("worker-b", self.coordinator.worker_states())

    def test_worker_cannot_propose_changes_outside_declared_scope(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "README.md").write_text("outside scope\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "outside declared ownership"):
            self.coordinator.prepare_proposal("worker-a")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "failed")
        artifacts = list((self.root / "isolated-workspaces" / ".proposals").glob("worker-a-*.patch"))
        self.assertEqual(len(artifacts), 1)
        self.coordinator.cleanup_worker("worker-a")
        self.assertFalse(workspace.exists())

    def test_adoption_checks_artifact_and_applies_only_owned_paths(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        adopted = self.coordinator.adopt(proposal)
        self.assertEqual(adopted, ("src/feature.py",))
        self.assertEqual((self.repo / "src" / "feature.py").read_text(encoding="utf-8"), "answer = 42\n")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "adopted")

    def test_disjoint_sibling_proposals_are_adopted_in_one_serialized_step(self):
        workspace_a = self.coordinator.create_worker("worker-a", ["src/feature_a.py"])
        workspace_b = self.coordinator.create_worker("worker-b", ["src/feature_b.py"])
        (workspace_a / "src" / "feature_a.py").write_text("answer_a = 1\\n", encoding="utf-8")
        (workspace_b / "src" / "feature_b.py").write_text("answer_b = 2\\n", encoding="utf-8")
        proposal_a = self.coordinator.prepare_proposal("worker-a")
        proposal_b = self.coordinator.prepare_proposal("worker-b")
        adopted = self.coordinator.adopt_many((proposal_a, proposal_b))
        self.assertEqual(adopted, {
            "worker-a": ("src/feature_a.py",),
            "worker-b": ("src/feature_b.py",),
        })
        self.assertEqual((self.repo / "src" / "feature_a.py").read_text(encoding="utf-8"), "answer_a = 1\\n")
        self.assertEqual((self.repo / "src" / "feature_b.py").read_text(encoding="utf-8"), "answer_b = 2\\n")

    def test_batch_adoption_rejects_duplicate_proposals_before_apply(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        with self.assertRaisesRegex(ValueError, "worker ids must be unique"):
            self.coordinator.adopt_many((proposal, proposal))
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_failed_post_apply_verification_rolls_back_target(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        import portable.graph_workspace_adoption as adoption_module
        real_git = adoption_module._git
        applied = {"value": False}

        def injected_git(root, *args, input_bytes=None):
            if args[:2] == ("apply", "--") and "--check" not in args:
                result = real_git(root, *args, input_bytes=input_bytes)
                applied["value"] = True
                return result
            if applied["value"] and args[:2] == ("diff", "--name-only"):
                return b"unexpected\\x00"
            return real_git(root, *args, input_bytes=input_bytes)

        with patch("portable.graph_workspace_adoption._git", side_effect=injected_git):
            with self.assertRaisesRegex(RuntimeError, "adopted path set differs"):
                self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "failed")

    def test_stale_target_head_blocks_adoption(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# changed base\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "advance target")
        with self.assertRaisesRegex(RuntimeError, "stale target HEAD"):
            self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_dirty_target_blocks_adoption_before_any_patch_is_applied(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# local edit\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "not clean"):
            self.coordinator.adopt(proposal)
        self.assertFalse((self.repo / "src" / "feature.py").exists())

    def test_failed_adoption_cancels_siblings_and_records_failure(self):
        workspace_a = self.coordinator.create_worker("worker-a", ["src/a.py"])
        self.coordinator.create_worker("worker-b", ["src/b.py"])
        (workspace_a / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        proposal = self.coordinator.prepare_proposal("worker-a")
        (self.repo / "README.md").write_text("# dirty target\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "not clean"):
            self.coordinator.adopt(proposal)
        states = self.coordinator.worker_states()
        self.assertEqual(states["worker-a"], "failed")
        self.assertEqual(states["worker-b"], "cancel_requested")
        self.assertTrue(Path(proposal.artifact_path).is_file())
        self.coordinator.cleanup_worker("worker-a")
        self.coordinator.mark_worker_stopped("worker-b")
        self.coordinator.cleanup_worker("worker-b")

    def test_cancellation_blocks_new_workers_and_does_not_claim_sibling_stopped(self):
        self.coordinator.create_worker("worker-a", ["src/a.py"])
        states = self.coordinator.cancel_siblings("hard safety failure")
        self.assertEqual(states, {"worker-a": "cancel_requested"})
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "cancel_requested")
        with self.assertRaisesRegex(RuntimeError, "hard safety failure"):
            self.coordinator.create_worker("worker-b", ["src/b.py"])
        self.coordinator.mark_worker_stopped("worker-a")
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "stopped")

    def test_cleanup_refuses_live_worker_and_removes_captured_proposal_safely(self):
        workspace = self.coordinator.create_worker("worker-a", ["src/feature.py"])
        (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "may still be running"):
            self.coordinator.cleanup_worker("worker-a")
        proposal = self.coordinator.prepare_proposal("worker-a")
        removed = self.coordinator.cleanup_worker("worker-a")
        self.assertEqual(Path(removed), workspace)
        self.assertFalse(workspace.exists())
        self.assertTrue(Path(proposal.artifact_path).is_file())
        self.assertEqual(self.coordinator.worker_states()["worker-a"], "cleaned")


if __name__ == "__main__":
    unittest.main()
