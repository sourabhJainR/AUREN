from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from portable.workflow_profile import WorkflowProfile
from portable.workflow_profile_store import (
    WorkflowProfileStoreError,
    load_profile,
    save_profile,
)


class WorkflowProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.root = Path(self._temporary_directory.name)

    def test_missing_profile_is_the_only_empty_state(self) -> None:
        path = self.root / "preferences" / "workflow.json"
        self.assertIsNone(load_profile(path))
        expected = WorkflowProfile(delivery_mode="stage_only", execution_mode="autonomous")
        self.assertEqual(expected, save_profile(path, expected))
        self.assertEqual(expected, load_profile(path))

    def test_profile_write_is_canonical_and_permissions_are_private_on_posix(self) -> None:
        path = self.root / "workflow.json"
        save_profile(path, WorkflowProfile())
        first = path.read_bytes()
        save_profile(path, WorkflowProfile())
        self.assertEqual(first, path.read_bytes())
        if os.name == "posix":
            self.assertEqual(0, path.stat().st_mode & 0o077)

    def test_invalid_existing_profile_does_not_fall_back_to_defaults(self) -> None:
        path = self.root / "workflow.json"
        path.write_text('{"profile_version":"unknown"}', encoding="utf-8")
        with self.assertRaises(WorkflowProfileStoreError):
            load_profile(path)

    def test_duplicate_keys_are_rejected(self) -> None:
        path = self.root / "workflow.json"
        path.write_text(
            '{"delivery_mode":"stage_only","delivery_mode":"working_tree"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(WorkflowProfileStoreError, "duplicate"):
            load_profile(path)

    def test_oversized_profile_is_rejected(self) -> None:
        path = self.root / "workflow.json"
        path.write_bytes(b" " * 16385)
        with self.assertRaisesRegex(WorkflowProfileStoreError, "size limit"):
            load_profile(path)

    def test_symlink_profile_is_rejected(self) -> None:
        target = self.root / "real.json"
        target.write_text(json.dumps(WorkflowProfile().as_dict()), encoding="utf-8")
        link = self.root / "workflow.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available on this platform")
        with self.assertRaisesRegex(WorkflowProfileStoreError, "symlink"):
            load_profile(link)
        with self.assertRaisesRegex(WorkflowProfileStoreError, "symlink"):
            save_profile(link, WorkflowProfile())

    def test_invalid_profile_is_not_written(self) -> None:
        path = self.root / "workflow.json"
        with self.assertRaises(WorkflowProfileStoreError):
            save_profile(path, {"profile_version": "unsupported"})
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()


    def test_symlink_profile_parent_is_rejected(self) -> None:
        external = self.root / "external"
        external.mkdir()
        parent_link = self.root / ".auren"
        try:
            parent_link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are not available on this platform")
        profile_path = parent_link / "workflow_profile.json"
        with self.assertRaisesRegex(WorkflowProfileStoreError, "symlink"):
            load_profile(profile_path)
        with self.assertRaisesRegex(WorkflowProfileStoreError, "symlink"):
            save_profile(profile_path, WorkflowProfile())
        self.assertFalse((external / "workflow_profile.json").exists())
