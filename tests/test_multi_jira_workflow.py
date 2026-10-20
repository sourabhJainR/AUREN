"""Regression tests for branch-aware multi-Jira workflow helpers."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from portable.multi_jira_workflow import (
    BranchAwareWorkspace,
    GitWorkflowError,
    ReadOnceFileCache,
    is_local_developer_setup,
    record_verified_jira_learning,
    plan_jira_batch,
)
from portable.jira_batch_planner import JiraWorkItem


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise AssertionError(f"git {args!r} failed: {result.stderr}")
    return result.stdout.strip()


class ReadOnceFileCacheTests(unittest.TestCase):
    def test_reuses_content_until_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "module.py"
            target.write_text("one\n", encoding="utf-8")
            cache = ReadOnceFileCache(root)

            self.assertEqual("one\n", cache.read_text("module.py"))
            self.assertEqual("one\n", cache.read_text("module.py"))
            self.assertEqual(1, cache.stats()["content_reads"])
            self.assertEqual(1, cache.stats()["cache_hits"])

            target.write_text("two and longer\n", encoding="utf-8")
            os.utime(target, ns=(target.stat().st_atime_ns, target.stat().st_mtime_ns + 2_000_000))
            self.assertEqual("two and longer\n", cache.read_text("module.py"))
            self.assertEqual(2, cache.stats()["content_reads"])

    def test_rejects_paths_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                ReadOnceFileCache(temp).read_text(outside)


class DeveloperSetupStagingTests(unittest.TestCase):
    def test_local_setup_classification_preserves_shared_templates(self) -> None:
        self.assertTrue(is_local_developer_setup(".env.local"))
        self.assertTrue(is_local_developer_setup(".venv/lib/site.py"))
        self.assertTrue(is_local_developer_setup(".vscode/settings.json"))
        self.assertTrue(is_local_developer_setup("service/local.settings.json"))
        self.assertFalse(is_local_developer_setup(".env.example"))
        self.assertFalse(is_local_developer_setup("src/settings.json"))

    def test_stage_paths_refuses_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init")
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "AUREN tests")
            (root / "safe.py").write_text("value = 1\n", encoding="utf-8")
            (root / ".env.local").write_text("TOKEN=do-not-commit\n", encoding="utf-8")
            workspace = BranchAwareWorkspace(root)

            with self.assertRaisesRegex(GitWorkflowError, "developer-local setup"):
                workspace.stage_paths(["safe.py", ".env.local"])
            self.assertEqual("", git(root, "diff", "--cached", "--name-only"))


class BranchAwareWorkspaceTests(unittest.TestCase):
    def test_starts_from_feature_then_fast_forwards_to_latest_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "remote.git"
            seed = root / "seed"
            clone = root / "clone"
            seed.mkdir()
            git(seed, "init", "-b", "main")
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "AUREN tests")
            (seed / "base.txt").write_text("base\n", encoding="utf-8")
            git(seed, "add", "base.txt")
            git(seed, "commit", "-m", "base")
            git(seed, "branch", "feature/example")
            git(seed, "init", "--bare", str(remote))
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "main")
            git(seed, "push", "origin", "feature/example")

            # Clone while the feature branch is behind, then advance remote main.
            git(seed, "clone", str(remote), str(clone))
            git(clone, "config", "user.email", "test@example.com")
            git(clone, "config", "user.name", "AUREN tests")
            (seed / "main-only.txt").write_text("latest main\\n", encoding="utf-8")
            git(seed, "add", "main-only.txt")
            git(seed, "commit", "-m", "advance main")
            git(seed, "push", "origin", "main")
            workspace = BranchAwareWorkspace(clone, remote="origin")

            result = workspace.start_task_branch(
                "jira/APP-123-safe-change",
                feature_branch="feature/example",
                target_branch="main",
            )
            snapshot = workspace.snapshot()
            self.assertEqual("jira/APP-123-safe-change", snapshot.branch)
            self.assertTrue(result.fetched)
            self.assertTrue((clone / "main-only.txt").exists())
            self.assertEqual(
                git(clone, "rev-parse", "origin/main"),
                git(clone, "rev-parse", "HEAD"),
            )

    def test_refuses_sync_when_worktree_is_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init")
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "AUREN tests")
            (root / "file.txt").write_text("initial\n", encoding="utf-8")
            git(root, "add", "file.txt")
            git(root, "commit", "-m", "initial")
            (root / "file.txt").write_text("uncommitted\n", encoding="utf-8")
            with self.assertRaisesRegex(GitWorkflowError, "dirty worktree"):
                BranchAwareWorkspace(root).sync_current_with_remote("main")


class JiraLearningTests(unittest.TestCase):
    def test_routes_only_evidence_backed_lesson_to_canonical_memory(self) -> None:
        class FakeMemory:
            def __init__(self) -> None:
                self.kwargs = None

            def remember(self, kind: str, lesson: str, **kwargs: object) -> dict[str, object]:
                self.kwargs = {"kind": kind, "lesson": lesson, **kwargs}
                return self.kwargs

        memory = FakeMemory()
        result = record_verified_jira_learning(
            memory,
            jira_key="app-123",
            lesson="Reuse the existing repository query helper.",
            evidence_ids=["test:123", "commit:abc"],
            intent_digest="intent-digest",
            approved=True,
        )
        self.assertEqual("procedural", result["kind"])
        self.assertEqual("APP-123", result["task_id"])
        self.assertTrue(result["verified"])
        self.assertEqual(("test:123", "commit:abc"), tuple(result["evidence_ids"]))

    def test_requires_evidence_before_learning_can_be_promoted(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            record_verified_jira_learning(
                object(),
                jira_key="APP-1",
                lesson="Unverified guess",
                evidence_ids=[],
                intent_digest="digest",
                approved=True,
            )


class JiraBatchPlannerIntegrationTests(unittest.TestCase):
    def test_multi_jira_workflow_delegates_readiness_without_persisting_state(self) -> None:
        items = (
            JiraWorkItem("APP-1", ("Recovery test passes",), likely_paths=("src/recovery.py",)),
            JiraWorkItem("APP-2", ("Dashboard shows progress",), depends_on=("APP-1",), likely_paths=("src/dashboard.py",)),
            JiraWorkItem("APP-3", ("Docs are updated",), likely_paths=("docs/ops.md",)),
        )
        wave = plan_jira_batch(items)
        self.assertEqual((("APP-1", "APP-3"),), wave.parallel_groups)
        self.assertEqual({"APP-2": ("APP-1",)}, dict(wave.blocked))

        next_wave = plan_jira_batch(items, completed=("APP-1",))
        self.assertEqual((("APP-2", "APP-3"),), next_wave.parallel_groups)
        self.assertEqual({}, dict(next_wave.blocked))


if __name__ == "__main__":
    unittest.main()
