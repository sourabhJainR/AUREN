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
            legacy = root / ".aer"
            legacy.mkdir()
            (legacy / "checkpoint.json").write_text('{"run":"last-known-good"}', encoding="utf-8")
            canonical = root / ".auren"
            pointer = root / "active-state-root"

            old_digest = inspect_state_migration(legacy, root / "unused").manifest_sha256
            migration = migrate_state_root(legacy, canonical, dry_run=False)
            self.assertEqual(migration.status, "copied_verified_source_retained")
            new_digest = inspect_state_migration(canonical, root / "unused2").manifest_sha256

            self.assertEqual(activate_state_root(canonical, pointer, expected_manifest_sha256=new_digest), str(canonical.resolve()))
            self.assertEqual(pointer.read_text().strip(), str(canonical.resolve()))

            self.assertEqual(rollback_state_root(legacy, pointer, expected_manifest_sha256=old_digest), str(legacy.resolve()))
            self.assertEqual(pointer.read_text().strip(), str(legacy.resolve()))
            self.assertTrue((legacy / "checkpoint.json").exists())
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
