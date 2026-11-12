from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.github_workspace import parse_github_repository_url, provision_github_workspace


class GitHubWorkspaceTests(unittest.TestCase):
    def test_parse_github_https_repository_url(self):
        self.assertEqual(
            ("sourabhJainR", "AUREN"),
            parse_github_repository_url("https://github.com/sourabhJainR/AUREN/"),
        )
        self.assertEqual(
            ("owner", "repo"),
            parse_github_repository_url("https://github.com/owner/repo.git"),
        )

    def test_rejects_non_github_or_credential_bearing_urls(self):
        invalid = (
            "http://github.com/owner/repo",
            "https://example.com/owner/repo",
            "https://user:secret@github.com/owner/repo",
            "https://github.com/owner/repo?token=secret",
            "https://github.com/owner",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_github_repository_url(value)

    def test_provision_clones_to_managed_workspace_and_initializes_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            target = home / "workspaces" / "sourabhJainR" / "AUREN"
            calls = []

            def fake_git(args, *, cwd=None, timeout=60):
                calls.append((args, cwd, timeout))
                if args[:2] == ["status", "--porcelain"]:
                    return ""
                if args[:3] == ["rev-parse", "--verify", "HEAD"]:
                    return "a" * 40
                return ""

            with (
                patch("portable.github_workspace.canonical_auren_home", return_value=home),
                patch("portable.github_workspace._git", side_effect=fake_git),
                patch("portable.onboarding.initialize_repository") as initialize,
            ):
                result = provision_github_workspace(
                    "https://github.com/sourabhJainR/AUREN", "main",
                )

            self.assertEqual(target, result)
            clone = next(call for call in calls if call[0][0] == "clone")
            self.assertIn("--branch", clone[0])
            self.assertIn("main", clone[0])
            self.assertEqual("https://github.com/sourabhJainR/AUREN.git", clone[0][-2])
            initialize.assert_called_once_with(target, apply=True)

    def test_local_host_accepts_repository_and_passes_managed_workspace(self):
        from portable import local_host

        with (
            patch("portable.github_workspace.provision_github_workspace", return_value=Path("C:/managed/AUREN")) as provision,
            patch("portable.local_host.serve_local", return_value=0) as serve,
        ):
            self.assertEqual(0, local_host.main([
                "--repository", "https://github.com/sourabhJainR/AUREN",
                "--branch", "main",
            ]))

        provision.assert_called_once_with("https://github.com/sourabhJainR/AUREN", "main")
        self.assertEqual("C:/managed/AUREN", serve.call_args.args[0])

    def test_branch_requires_repository(self):
        from portable import local_host

        with self.assertRaises(SystemExit):
            local_host.main(["--branch", "main"])


if __name__ == "__main__":
    unittest.main()
