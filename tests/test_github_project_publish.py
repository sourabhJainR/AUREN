"""Tests for explicit, clean-worktree GitHub project publishing."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.github_project_publish import publish_project


class GitHubProjectPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / ".git").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    @patch("portable.github_project_publish.shutil.which", return_value="/usr/bin/gh")
    @patch("portable.github_project_publish._run")
    def test_publish_verifies_remote_and_exact_head(self, run, _which):
        run.side_effect = [
            "true", "main", "", "", "", "", "git@github.com:owner/project.git",
            json.dumps({
                "nameWithOwner": "owner/project",
                "url": "https://github.com/owner/project",
                "visibility": "PRIVATE",
                "defaultBranchRef": {"name": "main"},
            }),
            "a" * 40, f"{'a' * 40}\trefs/heads/main",
        ]
        result = publish_project(self.root, "owner/project")
        self.assertEqual("published", result["status"])
        self.assertEqual("private", result["visibility"])
        self.assertEqual("a" * 40, result["head_commit"])
        create_call = run.call_args_list[5].args[0]
        self.assertIn("--private", create_call)
        self.assertIn("--push", create_call)

    @patch("portable.github_project_publish.shutil.which", return_value="/usr/bin/gh")
    @patch("portable.github_project_publish._run")
    def test_dirty_worktree_is_never_staged_or_published(self, run, _which):
        run.side_effect = ["true", "main", "?? secrets.txt"]
        with self.assertRaises(PermissionError):
            publish_project(self.root, "owner/project")
        self.assertEqual(3, run.call_count)

    @patch("portable.github_project_publish.shutil.which", return_value="/usr/bin/gh")
    @patch("portable.github_project_publish._run")
    def test_existing_origin_is_never_replaced(self, run, _which):
        run.side_effect = ["true", "main", "", "origin"]
        with self.assertRaises(FileExistsError):
            publish_project(self.root, "owner/project")
        self.assertEqual(4, run.call_count)

    @patch("portable.github_project_publish.shutil.which", return_value="/usr/bin/gh")
    @patch("portable.github_project_publish._run")
    def test_origin_must_match_exact_owner_and_repository(self, run, _which):
        run.side_effect = [
            "true", "main", "", "", "", "", "git@github.com:someone-else/project.git",
            json.dumps({
                "nameWithOwner": "owner/project",
                "url": "https://github.com/owner/project",
                "visibility": "PRIVATE",
            }),
        ]
        with self.assertRaisesRegex(RuntimeError, "origin owner/name"):
            publish_project(self.root, "owner/project")

    @patch("portable.github_project_publish.shutil.which", return_value="/usr/bin/gh")
    @patch("portable.github_project_publish._run")
    def test_invalid_github_metadata_fails_with_clear_error(self, run, _which):
        run.side_effect = ["true", "main", "", "", "", "", "git@github.com:owner/project.git", "not-json"]
        with self.assertRaisesRegex(RuntimeError, "invalid repository metadata"):
            publish_project(self.root, "owner/project")

    def test_invalid_repository_name_rejected_before_commands(self):
        with patch("portable.github_project_publish._run") as run:
            with self.assertRaises(ValueError):
                publish_project(self.root, "../project")
            run.assert_not_called()

    def test_public_visibility_must_be_explicitly_selected(self):
        with patch("portable.github_project_publish._run") as run:
            with self.assertRaises(ValueError):
                publish_project(self.root, "owner/project", visibility="world")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
