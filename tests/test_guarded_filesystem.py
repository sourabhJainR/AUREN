from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from portable.guarded_filesystem import (
    read_workspace_bytes,
    read_workspace_text,
    resolve_workspace_path,
    write_workspace_text,
)


class GuardedFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.outside = Path(self.temp.name) / "outside"
        self.outside.mkdir()

    def test_root_confined_read_and_write(self):
        target = write_workspace_text(self.root, "docs/design/brief.md", "safe\n")
        self.assertEqual("safe\n", read_workspace_text(self.root, "docs/design/brief.md"))
        self.assertIn(read_workspace_bytes(self.root, "docs/design/brief.md"), (b"safe\n", b"safe\r\n"))
        self.assertTrue(target.is_file())
        self.assertTrue(self.root.resolve() in target.resolve().parents)

    def test_traversal_and_absolute_paths_are_rejected(self):
        for path in ("../outside/secret.txt", "docs/../../outside/secret.txt", "/tmp/outside.txt", "C:\\temp\\outside.txt", ""):
            with self.subTest(path=path):
                with self.assertRaises((ValueError, PermissionError)):
                    resolve_workspace_path(self.root, path)
                with self.assertRaises((ValueError, PermissionError)):
                    write_workspace_text(self.root, path, "blocked")

    def test_symlink_parent_escape_is_rejected_before_io(self):
        link = self.root / "linked"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable on this runner: {type(exc).__name__}")
        with self.assertRaises(PermissionError):
            write_workspace_text(self.root, "linked/escape.txt", "blocked")
        self.assertFalse((self.outside / "escape.txt").exists())

    def test_symlink_leaf_is_rejected_before_read_or_write(self):
        outside_file = self.outside / "secret.txt"
        outside_file.write_text("secret", encoding="utf-8")
        link = self.root / "secret.txt"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable on this runner: {type(exc).__name__}")
        with self.assertRaises(PermissionError):
            read_workspace_text(self.root, "secret.txt")
        with self.assertRaises(PermissionError):
            write_workspace_text(self.root, "secret.txt", "overwritten")
        self.assertEqual("secret", outside_file.read_text(encoding="utf-8"))

    def test_symlinked_intermediate_component_cannot_be_followed(self):
        real = self.root / "real"
        real.mkdir()
        (real / "file.txt").write_text("inside", encoding="utf-8")
        link = self.root / "alias"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable on this runner: {type(exc).__name__}")
        with self.assertRaises(PermissionError):
            read_workspace_text(self.root, "alias/file.txt")


if __name__ == "__main__":
    unittest.main()
