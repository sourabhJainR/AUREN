from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.state_migration import inspect_state_migration, migrate_state_root


class StateMigrationTests(unittest.TestCase):
    def test_dry_run_does_not_change_either_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            source.mkdir()
            (source / "memory.db").write_bytes(b"legacy-state")
            destination = root / ".auren"

            result = migrate_state_root(source, destination, dry_run=True)

            self.assertEqual(result.status, "dry_run")
            self.assertFalse(destination.exists())
            self.assertEqual((source / "memory.db").read_bytes(), b"legacy-state")
            self.assertEqual(result.byte_count, len(b"legacy-state"))
            self.assertTrue(result.manifest_sha256)

    def test_copy_verifies_destination_and_retains_legacy_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "checkpoint.json").write_text('{"step":2}', encoding="utf-8")
            destination = root / ".auren"

            result = migrate_state_root(source, destination, dry_run=False)

            self.assertEqual(result.status, "copied_verified_source_retained")
            self.assertEqual(
                (destination / "nested" / "checkpoint.json").read_text(encoding="utf-8"),
                '{"step":2}',
            )
            self.assertEqual(
                (source / "nested" / "checkpoint.json").read_text(encoding="utf-8"),
                '{"step":2}',
            )
            self.assertEqual(result.manifest_sha256, inspect_state_migration(source, destination).manifest_sha256)

            rerun = migrate_state_root(source, destination, dry_run=False)
            self.assertEqual(rerun.status, "already_migrated_verified")
            self.assertEqual(rerun.manifest_sha256, result.manifest_sha256)

    def test_existing_conflicting_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            destination = root / ".auren"
            source.mkdir()
            destination.mkdir()
            (source / "state").write_text("old", encoding="utf-8")
            (destination / "state").write_text("new", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                migrate_state_root(source, destination, dry_run=False)

            self.assertEqual((source / "state").read_text(encoding="utf-8"), "old")
            self.assertEqual((destination / "state").read_text(encoding="utf-8"), "new")

    def test_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            source.mkdir()
            target = root / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            (source / "linked.txt").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                migrate_state_root(source, root / ".auren", dry_run=True)

    def test_source_and_destination_cannot_nest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "destination must not be the source or a child"):
                migrate_state_root(source, source / ".auren", dry_run=True)


    def test_interrupted_copy_cleans_staging_and_preserves_source_for_retry(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            source.mkdir()
            original = source / "memory.db"
            original.write_bytes(b"last-known-good-state")
            destination = root / ".auren"

            with patch(
                "portable.state_migration.shutil.copy2",
                side_effect=OSError("simulated interruption during copy"),
            ):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    migrate_state_root(source, destination, dry_run=False)

            self.assertFalse(destination.exists())
            self.assertEqual(original.read_bytes(), b"last-known-good-state")
            self.assertFalse((root / ".auren.migration.lock").exists())
            self.assertFalse(any(path.name.startswith(".auren.migration-") for path in root.iterdir()))

            # A fresh attempt can proceed after the failed copy was cleaned up.
            result = migrate_state_root(source, destination, dry_run=False)
            self.assertEqual(result.status, "copied_verified_source_retained")
            self.assertEqual((destination / "memory.db").read_bytes(), b"last-known-good-state")


if __name__ == "__main__":
    unittest.main()
