from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from portable.graph_workspace_adoption import GraphWorkspaceCoordinator


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True, timeout=20)
    return result.stdout.strip()


class DurableGraphWorkspaceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        git(self.repo, "config", "user.name", "AUREN Test")
        git(self.repo, "config", "user.email", "auren@example.invalid")
        (self.repo / "README.md").write_text("seed\\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "seed")
        self.workspaces = self.root / "workspaces"
        self.registry = self.root / "state" / "registry.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_coordinator_restores_worker_ownership_and_blocks_duplicate_id(self):
        first = GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        second = GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        self.assertEqual(second.worker_states(), {"worker-a": "unknown"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            second.create_worker("worker-a", ["src/a.py"])
        with self.assertRaisesRegex(PermissionError, "overlaps"):
            second.create_worker("worker-b", ["src"])
        with self.assertRaisesRegex(RuntimeError, "may still be running"):
            second.cleanup_worker("worker-a")
        with self.assertRaisesRegex(ValueError, "evidence is required"):
            second.confirm_recovered_worker_stopped("worker-a", evidence="")
        second.confirm_recovered_worker_stopped("worker-a", evidence="supervisor process table confirms pid exited")
        self.assertEqual(second.worker_states()["worker-a"], "stopped")
        second.cleanup_worker("worker-a")

    def test_fresh_coordinator_restores_proposal_and_adopts_only_verified_artifact(self):
        first = GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = first.prepare_proposal("worker-a")
        second = GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        restored = second._proposals["worker-a"]
        self.assertEqual(restored.artifact_digest, proposal.artifact_digest)
        self.assertEqual(second.adopt(restored), ("src/a.py",))
        self.assertEqual((self.repo / "src" / "a.py").read_text(encoding="utf-8"), "answer = 42\\n")

    def test_missing_worktree_is_not_treated_as_empty(self):
        first = GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        workspace = first.create_worker("worker-a", ["src/a.py"])
        git(self.repo, "worktree", "remove", "--force", str(workspace))
        with self.assertRaisesRegex(RuntimeError, "missing or unsafe"):
            GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)

    def test_registry_file_is_private_and_symlink_is_rejected(self):
        GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)
        linked = self.root / "linked.sqlite3"
        linked.symlink_to(self.registry)
        with self.assertRaisesRegex(PermissionError, "must not be a symlink"):
            GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=linked)


if __name__ == "__main__":
    unittest.main()
