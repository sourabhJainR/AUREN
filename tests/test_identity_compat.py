from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.identity_compat import auren_home, get_auren_env, project_state_dir
from portable.state_migration import migrate_state_directory


class IdentityCompatibilityTests(unittest.TestCase):
    def test_canonical_environment_precedes_legacy_alias(self) -> None:
        env = {"AUREN_NETWORK_ALLOWED": "0", "AER_NETWORK_ALLOWED": "1"}
        self.assertEqual(get_auren_env("NETWORK_ALLOWED", "1", environ=env), "0")

    def test_legacy_environment_is_fallback_only(self) -> None:
        self.assertEqual(
            get_auren_env("LOCAL_LLM_ENABLED", "0", environ={"AER_LOCAL_LLM_ENABLED": "1"}),
            "1",
        )
        self.assertEqual(
            get_auren_env("LOCAL_LLM_ENABLED", "0", environ={}),
            "0",
        )

    def test_home_selection_preserves_existing_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(auren_home(home=root, environ={}), root / ".auren")
            (root / ".aer").mkdir()
            self.assertEqual(auren_home(home=root, environ={}), root / ".aer")
            (root / ".auren").mkdir()
            self.assertEqual(auren_home(home=root, environ={}), root / ".auren")
            self.assertEqual(
                auren_home(home=root, environ={"AUREN_HOME": "~/chosen", "AER_HOME": "~/legacy"}),
                Path("~/chosen"),
            )

    def test_project_state_prefers_canonical_then_legacy_without_moving_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / ".aer"
            legacy.mkdir()
            self.assertEqual(project_state_dir(root), legacy)
            canonical = root / ".auren"
            canonical.mkdir()
            self.assertEqual(project_state_dir(root), canonical)
            self.assertTrue(legacy.is_dir())

    def test_state_migration_is_dry_run_then_verified_and_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            destination = root / ".auren"
            source.mkdir()
            (source / "memory.db").write_bytes(b"known-state")
            planned = migrate_state_directory(source, destination)
            self.assertEqual(planned["status"], "planned")
            self.assertFalse(destination.exists())
            migrated = migrate_state_directory(source, destination, dry_run=False)
            self.assertEqual(migrated["status"], "verified")
            self.assertEqual((destination / "memory.db").read_bytes(), b"known-state")
            self.assertTrue(source.is_dir())
            self.assertEqual(
                migrate_state_directory(source, destination, dry_run=False)["status"],
                "already_migrated",
            )

    def test_state_migration_refuses_divergent_destination_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            destination = root / ".auren"
            source.mkdir()
            (source / "state.json").write_text('{"v":1}', encoding="utf-8")
            destination.mkdir()
            (destination / "state.json").write_text('{"v":2}', encoding="utf-8")
            with self.assertRaises(FileExistsError):
                migrate_state_directory(source, destination, dry_run=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / ".aer"
            source.mkdir()
            target = root / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            (source / "link.txt").symlink_to(target)
            with self.assertRaises(ValueError):
                migrate_state_directory(source, root / ".auren", dry_run=False)


if __name__ == "__main__":
    unittest.main()
