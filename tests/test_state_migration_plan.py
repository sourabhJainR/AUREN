from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.state_migration_plan import plan_state_migration
from portable.state_paths import StatePathConflict


class StateMigrationPlanTests(unittest.TestCase):
    def test_plan_is_read_only_and_reports_copy_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / ".aer"
            canonical = base / ".auren"
            source = legacy / "sessions" / "session-1.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"session_id":"session-1"}\n', encoding="utf-8")
            (legacy / "custom-extension.bin").write_bytes(b"leave me alone")

            plan = plan_state_migration(canonical, legacy)

            self.assertTrue(plan["safe_to_apply"])
            self.assertEqual(plan["summary"]["copy"], 1)
            self.assertEqual(plan["items"][0]["path"], "sessions/session-1.json")
            self.assertEqual(plan["items"][0]["action"], "copy")
            self.assertEqual(plan["unknown_files_left_untouched"], ["custom-extension.bin"])
            self.assertFalse(canonical.exists(), "preflight must not create the canonical state root")
            self.assertTrue(source.is_file(), "preflight must preserve the legacy source")
            self.assertFalse((canonical / ".state-migration.json").exists())

    def test_plan_reports_conflict_without_mutating_either_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / ".aer"
            canonical = base / ".auren"
            source = legacy / "sessions" / "session-1.json"
            target = canonical / "sessions" / "session-1.json"
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_text('{"session_id":"legacy"}\n', encoding="utf-8")
            target.write_text('{"session_id":"canonical"}\n', encoding="utf-8")

            plan = plan_state_migration(canonical, legacy)

            self.assertFalse(plan["safe_to_apply"])
            self.assertEqual(plan["conflicts"], ["sessions/session-1.json"])
            self.assertEqual(plan["items"][0]["action"], "conflict")
            self.assertIn("neither would be overwritten", plan["items"][0]["reason"])
            self.assertEqual(source.read_text(encoding="utf-8"), '{"session_id":"legacy"}\n')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"session_id":"canonical"}\n')

    def test_plan_identifies_existing_identical_target_without_writing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / ".aer"
            canonical = base / ".auren"
            source = legacy / "active.json"
            target = canonical / "active.json"
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_text('{"version":1}\n', encoding="utf-8")
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            plan = plan_state_migration(canonical, legacy)

            self.assertTrue(plan["safe_to_apply"])
            self.assertEqual(plan["items"][0]["action"], "record_existing_identical")
            self.assertFalse((canonical / ".state-migration.json").exists())


if __name__ == "__main__":
    unittest.main()
