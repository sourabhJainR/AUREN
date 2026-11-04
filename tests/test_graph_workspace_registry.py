from __future__ import annotations

import os
import sqlite3
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
        self.coordinators = []

    def make_coordinator(self):
        coordinator = GraphWorkspaceCoordinator(
            self.repo, workspace_root=self.workspaces, registry_path=self.registry
        )
        self.coordinators.append(coordinator)
        return coordinator

    def tearDown(self):
        for coordinator in self.coordinators:
            coordinator.close()
        self.temp.cleanup()

    def test_fresh_coordinator_restores_worker_ownership_and_blocks_duplicate_id(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        second = self.make_coordinator()
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
        third = self.make_coordinator()
        self.assertEqual(third.worker_states()["worker-a"], "stopped")
        db = sqlite3.connect(self.registry)
        try:
            evidence = db.execute(
                "SELECT termination_evidence FROM graph_workspace_registry WHERE worker_id = ?",
                ("worker-a",),
            ).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(evidence, "supervisor process table confirms pid exited")
        with self.assertRaisesRegex(PermissionError, "no persisted proposal artifact"):
            second.cleanup_worker("worker-a")

    def test_fresh_coordinator_restores_proposal_and_adopts_only_verified_artifact(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = first.prepare_proposal("worker-a")
        second = self.make_coordinator()
        restored = second._proposals["worker-a"]
        self.assertEqual(restored.artifact_digest, proposal.artifact_digest)
        self.assertEqual(second.adopt(restored), ("src/a.py",))
        self.assertEqual((self.repo / "src" / "a.py").read_text(encoding="utf-8"), "answer = 42\\n")
        third = self.make_coordinator()
        self.assertEqual(third.worker_states()["worker-a"], "adopted")
        with self.assertRaisesRegex(RuntimeError, "commit recovered graph adoption"):
            third.create_worker("worker-b", ["src/b.py"])

    def test_cleaned_worktree_tombstone_restores_without_reserving_paths(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        first.prepare_proposal("worker-a")
        first.cleanup_worker("worker-a")

        second = self.make_coordinator()
        self.assertEqual(second.worker_states(), {"worker-a": "cleaned"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            second.create_worker("worker-a", ["src/a.py"])
        # The old cleaned lease must not block a new worker from owning its path.
        second.create_worker("worker-b", ["src/a.py"])

    def test_missing_worktree_is_not_treated_as_empty(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        git(self.repo, "worktree", "remove", "--force", str(workspace))
        with self.assertRaisesRegex(RuntimeError, "missing or unsafe"):
            self.make_coordinator()

    def test_registry_file_permissions_and_symlink_rejection(self):
        self.make_coordinator()
        if os.name == "posix":
            self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)
        linked = self.root / "linked.sqlite3"
        linked.symlink_to(self.registry)
        with self.assertRaisesRegex(PermissionError, "must not be a symlink"):
            GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=linked)


    def test_restart_reconciles_crash_before_apply_without_replaying_completed_work(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = first.prepare_proposal("worker-a")
        first._persist_adoption_intent(("worker-a",), proposal.paths, proposal.patch)
        first.close()

        second = self.make_coordinator()
        self.assertEqual(second.worker_states()["worker-a"], "proposal_ready")
        db = sqlite3.connect(self.registry)
        try:
            state = db.execute("SELECT state FROM graph_workspace_adoptions WHERE state='not_applied'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(state, "not_applied")

    def test_restart_blocks_partial_target_mutation_without_false_success(self):
        first = self.make_coordinator()
        workspace = first.create_worker("worker-a", ["src/a.py"])
        (workspace / "src").mkdir()
        (workspace / "src" / "a.py").write_text("answer = 42\\n", encoding="utf-8")
        proposal = first.prepare_proposal("worker-a")
        first._persist_adoption_intent(("worker-a",), proposal.paths, proposal.patch)
        first.close()

        (self.repo / "src").mkdir()
        (self.repo / "src" / "a.py").write_text("partial write\\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "dirty target"):
            GraphWorkspaceCoordinator(self.repo, workspace_root=self.workspaces, registry_path=self.registry)
        db = sqlite3.connect(self.registry)
        try:
            state = db.execute("SELECT state FROM graph_workspace_adoptions WHERE state='blocked'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(state, "blocked")


if __name__ == "__main__":
    unittest.main()
