from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "migrate_auren_state.py"


class StateMigrationCliTests(unittest.TestCase):
    def invoke(self, source: Path, destination: Path, *, apply: bool = False):
        command = [
            sys.executable, str(CLI),
            "--source", str(source),
            "--destination", str(destination),
        ]
        if apply:
            command.append("--apply")
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    def test_dry_run_is_default_and_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            destination = root / "canonical"
            source.mkdir()
            (source / "memory.json").write_text('{"entries": 2}\n', encoding="utf-8")
            result = self.invoke(source, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "dry_run")
            self.assertFalse(destination.exists())
            self.assertTrue(source.exists())
            self.assertTrue(payload["source_retained_for_rollback"])
            self.assertEqual(payload["file_count"], 1)
            self.assertEqual(len(payload["manifest_sha256"]), 64)

    def test_apply_retains_source_and_rerun_is_idempotently_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            destination = root / "canonical"
            source.mkdir()
            (source / "memory.json").write_text('{"entries": 2}\n', encoding="utf-8")
            first = self.invoke(source, destination, apply=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["status"], "copied_verified_source_retained")
            self.assertTrue(source.exists())
            self.assertEqual((source / "memory.json").read_bytes(), (destination / "memory.json").read_bytes())
            second = self.invoke(source, destination, apply=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["status"], "already_migrated_verified")

    def test_conflicting_destination_fails_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            destination = root / "canonical"
            source.mkdir()
            destination.mkdir()
            (source / "memory.json").write_text("legacy\n", encoding="utf-8")
            (destination / "memory.json").write_text("canonical\n", encoding="utf-8")
            result = self.invoke(source, destination, apply=True)
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual((destination / "memory.json").read_text(encoding="utf-8"), "canonical\n")
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
