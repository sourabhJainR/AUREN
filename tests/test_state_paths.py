from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.state_paths import AurenStatePaths, StatePathConflict


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS values_table (value TEXT NOT NULL)")
        db.execute("INSERT INTO values_table(value) VALUES(?)", (value,))


class AurenStatePathsTests(unittest.TestCase):
    def test_fresh_install_uses_canonical_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".auren"
            paths = AurenStatePaths.prepare(root)
            self.assertEqual(paths.root, root.resolve())
            self.assertTrue(paths.root.is_dir())
            self.assertFalse(paths.migration_manifest.exists())

    def test_legacy_sqlite_and_session_state_are_copied_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source_db = legacy / "memory" / "memory.db"
            _database(source_db, "keep-me")
            source_session = legacy / "sessions" / "session-1.json"
            source_session.parent.mkdir(parents=True)
            source_session.write_text(json.dumps({"session_id": "session-1", "stage": "resume"}), encoding="utf-8")

            paths = AurenStatePaths.prepare(canonical, legacy)
            target_db = canonical / "memory" / "memory.db"
            target_session = canonical / "sessions" / "session-1.json"
            self.assertTrue(target_db.is_file())
            self.assertTrue(source_db.is_file(), "migration must preserve the legacy source")
            with sqlite3.connect(target_db) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "keep-me")
            self.assertEqual(json.loads(target_session.read_text(encoding="utf-8"))["stage"], "resume")
            self.assertTrue(paths.migration_manifest.is_file())

    def test_migration_is_idempotent_after_canonical_state_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "memory" / "memory.db"
            _database(source, "legacy")
            AurenStatePaths.prepare(canonical, legacy)
            target = canonical / "memory" / "memory.db"
            _database(target, "new-canonical-write")
            AurenStatePaths.prepare(canonical, legacy)
            with sqlite3.connect(target) as db:
                values = {row[0] for row in db.execute("SELECT value FROM values_table")}
            self.assertEqual(values, {"legacy", "new-canonical-write"})

    def test_conflicting_existing_roots_fail_without_overwriting_either(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source, target = legacy / "memory" / "memory.db", canonical / "memory" / "memory.db"
            _database(source, "legacy")
            _database(target, "canonical")
            with self.assertRaisesRegex(StatePathConflict, "Both legacy and canonical state"):
                AurenStatePaths.prepare(canonical, legacy)
            with sqlite3.connect(source) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "legacy")
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "canonical")

    def test_migration_rejects_changed_legacy_copy_after_initial_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "memory" / "memory.db"
            _database(source, "initial")
            AurenStatePaths.prepare(canonical, legacy)
            _database(source, "legacy-was-written-again")
            with self.assertRaisesRegex(StatePathConflict, "changed after migration"):
                AurenStatePaths.prepare(canonical, legacy)


if __name__ == "__main__":
    unittest.main()
