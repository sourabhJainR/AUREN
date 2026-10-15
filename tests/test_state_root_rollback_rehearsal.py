from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.state_migration import inspect_state_migration, migrate_state_root
from portable.state_root_activation import activate_state_root, rollback_state_root


class StateRootRollbackRehearsalTests(unittest.TestCase):
    def test_migrate_activate_and_rollback_preserves_both_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "prior-state"
            source_root.mkdir()
            (source_root / "checkpoint.json").write_text('{"run":"last-known-good"}', encoding="utf-8")
            canonical = root / ".auren"
            pointer = root / "active-state-root"

            old_digest = inspect_state_migration(source_root, root / "unused").manifest_sha256
            migration = migrate_state_root(source_root, canonical, dry_run=False)
            self.assertEqual(migration.status, "copied_verified_source_retained")
            new_digest = inspect_state_migration(canonical, root / "unused2").manifest_sha256

            self.assertEqual(activate_state_root(canonical, pointer, expected_manifest_sha256=new_digest), str(canonical.resolve()))
            self.assertEqual(pointer.read_text().strip(), str(canonical.resolve()))

            self.assertEqual(rollback_state_root(source_root, pointer, expected_manifest_sha256=old_digest), str(source_root.resolve()))
            self.assertEqual(pointer.read_text().strip(), str(source_root.resolve()))
            self.assertTrue((source_root / "checkpoint.json").exists())
            self.assertTrue((canonical / "checkpoint.json").exists())

    def test_wrong_digest_never_changes_active_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".auren"
            state.mkdir()
            (state / "db").write_text("ok", encoding="utf-8")
            pointer = root / "active-state-root"
            pointer.write_text("/previous/root\n")
            with self.assertRaisesRegex(ValueError, "approved manifest"):
                activate_state_root(state, pointer, expected_manifest_sha256="0" * 64)
            self.assertEqual(pointer.read_text(), "/previous/root\n")


if __name__ == "__main__":
    unittest.main()
