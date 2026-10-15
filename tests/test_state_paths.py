from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from portable.session_state import SessionCheckpoint, SessionStore
from portable.state_paths import AurenStatePaths, StatePathConflict


@contextmanager
def _closed_connection(path: Path):
    """SQLite's connection context commits but does not close its file handle."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _closed_connection(path) as db:
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
            with _closed_connection(target_db) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "keep-me")
            self.assertEqual(json.loads(target_session.read_text(encoding="utf-8"))["stage"], "resume")
            self.assertTrue(paths.migration_manifest.is_file())

    def test_legacy_orchestration_and_observability_files_are_copied_non_destructively(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            sources = {
                "orchestration/active.py": "PLAN_VERSION = 1\n",
                "orchestration/previous.py": "PLAN_VERSION = 0\n",
                "orchestration/promotion.jsonl": '{"event":"promoted"}\n',
                "observability/traces.jsonl": '{"trace_id":"legacy-trace"}\n',
                "history.jsonl": '{"event":"installed"}\n',
                "active.json": '{"version":"1.0"}\n',
            }
            for relative, payload in sources.items():
                path = legacy / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload, encoding="utf-8")

            paths = AurenStatePaths.prepare(canonical, legacy)
            manifest = json.loads(paths.migration_manifest.read_text(encoding="utf-8"))
            for relative, payload in sources.items():
                self.assertEqual((canonical / relative).read_text(encoding="utf-8"), payload)
                self.assertEqual((legacy / relative).read_text(encoding="utf-8"), payload)
                self.assertIn(relative, manifest["files"])

            # Reruns verify source identity and preserve canonical writes.
            with (canonical / "orchestration" / "active.py").open("a", encoding="utf-8") as stream:
                stream.write("# canonical update\n")
            AurenStatePaths.prepare(canonical, legacy)
            self.assertIn("# canonical update", (canonical / "orchestration" / "active.py").read_text(encoding="utf-8"))
            self.assertEqual((legacy / "orchestration" / "active.py").read_text(encoding="utf-8"), sources["orchestration/active.py"])

    def test_migration_preserves_uncheckpointed_wal_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "memory" / "memory.db"
            source.parent.mkdir(parents=True)
            writer = sqlite3.connect(source)
            try:
                mode = writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")
                writer.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
                writer.execute("INSERT INTO values_table(value) VALUES('checkpointed')")
                writer.commit()
                writer.execute("INSERT INTO values_table(value) VALUES('wal-only')")
                writer.commit()
                wal_file = Path(str(source) + "-wal")
                self.assertTrue(wal_file.is_file())
                self.assertGreater(wal_file.stat().st_size, 0)

                AurenStatePaths.prepare(canonical, legacy)
                target = canonical / "memory" / "memory.db"
                with _closed_connection(target) as db:
                    values = {row[0] for row in db.execute("SELECT value FROM values_table")}
                self.assertEqual(values, {"checkpointed", "wal-only"})
                self.assertTrue(source.is_file(), "legacy SQLite source must remain available for rollback")
            finally:
                writer.close()

    def test_interrupted_copy_before_manifest_commit_recovers_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "sessions" / "interrupted.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"session_id":"interrupted","stage":"resume"}\n', encoding="utf-8")
            target = canonical / "sessions" / "interrupted.json"

            # Simulate process interruption after atomic target replacement but
            # before the migration manifest records the completed copy.
            with patch.object(AurenStatePaths, "_write_manifest", side_effect=OSError("simulated crash")):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    AurenStatePaths.prepare(canonical, legacy)

            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertTrue(source.is_file(), "legacy source must remain available for rollback")
            self.assertFalse((canonical / ".state-migration.json").exists())

            # Restart must recognize the identical target, record it, and not
            # overwrite either side or leave temporary files behind.
            paths = AurenStatePaths.prepare(canonical, legacy)
            self.assertTrue(paths.migration_manifest.is_file())
            manifest = json.loads(paths.migration_manifest.read_text(encoding="utf-8"))
            self.assertIn("sessions/interrupted.json", manifest["files"])
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertFalse(list(canonical.rglob("*.tmp")))

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
            with _closed_connection(target) as db:
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
            with _closed_connection(source) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "legacy")
            with _closed_connection(target) as db:
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

    def test_migration_rejects_symlinks_before_copying_any_legacy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            outside = base / "outside.json"
            outside.write_text('{"private":"outside-root"}', encoding="utf-8")
            linked = legacy / "sessions" / "linked.json"
            linked.parent.mkdir(parents=True)
            linked.symlink_to(outside)

            with self.assertRaisesRegex(StatePathConflict, "symbolic link: sessions/linked.json"):
                AurenStatePaths.prepare(canonical, legacy)

            self.assertFalse((canonical / "sessions" / "linked.json").exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), '{"private":"outside-root"}')
            self.assertTrue(linked.is_symlink(), "migration must not modify the legacy source")

    def test_migration_rejects_legacy_root_symlink_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_legacy = base / "real-legacy"
            real_legacy.mkdir()
            (real_legacy / "active.json").write_text('{"version":1}', encoding="utf-8")
            legacy_link = base / ".aer"
            legacy_link.symlink_to(real_legacy, target_is_directory=True)
            canonical = base / ".auren"

            with self.assertRaisesRegex(StatePathConflict, "Legacy state root is a symbolic link"):
                AurenStatePaths.prepare(canonical, legacy_link)

            self.assertFalse(canonical.exists())
            self.assertTrue(legacy_link.is_symlink())
            self.assertEqual((real_legacy / "active.json").read_text(encoding="utf-8"), '{"version":1}')


class SessionCheckpointDurabilityTests(unittest.TestCase):
    def test_checkpoint_round_trips_through_a_fresh_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            store = SessionStore(root)
            checkpoint = SessionCheckpoint(
                session_id="durable-session",
                task_id="task-17",
                project_key="project-key",
                stage="verify",
                completed_batches=["plan", "implement"],
                remaining_batches=["review", "merge"],
                active_provider="local",
                attempt=3,
                last_error=None,
                project_root="/workspace/project",
                intent="verify durable checkpoint state",
            )

            path = store.save(checkpoint)
            restored = SessionStore(root).load("durable-session")

            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.task_id, "task-17")
            self.assertEqual(restored.stage, "verify")
            self.assertEqual(restored.completed_batches, ["plan", "implement"])
            self.assertEqual(restored.remaining_batches, ["review", "merge"])
            self.assertEqual(restored.attempt, 3)
            self.assertEqual(restored.project_root, "/workspace/project")
            self.assertEqual(restored.intent, "verify durable checkpoint state")
            self.assertEqual(restored.state_digest, checkpoint.state_digest)
            self.assertEqual(path.read_text(encoding="utf-8").endswith("\n"), True)
            self.assertEqual(list(root.iterdir()), [path])

    def test_checkpoint_writes_use_unique_temporary_files_and_leave_no_debris(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            store = SessionStore(root)
            original_replace = os.replace
            temp_names: list[str] = []

            def capture_replace(source, target):
                temp_names.append(Path(source).name)
                return original_replace(source, target)

            with patch("portable.session_state.os.replace", side_effect=capture_replace):
                store.save(SessionCheckpoint(
                    session_id="same-session",
                    task_id="task-one",
                    project_key="project-key",
                    stage="implement",
                ))
                store.save(SessionCheckpoint(
                    session_id="same-session",
                    task_id="task-two",
                    project_key="project-key",
                    stage="verify",
                ))

            self.assertEqual(len(temp_names), 2)
            self.assertNotEqual(temp_names[0], temp_names[1])
            self.assertEqual(len(list(root.iterdir())), 1)
            restored = SessionStore(root).load("same-session")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.task_id, "task-two")
            self.assertEqual(restored.stage, "verify")

    def test_checkpoint_round_trips_nested_unicode_payload_without_digest_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            store = SessionStore(root)
            checkpoint = SessionCheckpoint(
                session_id="unicode-session",
                task_id="task-ünicode",
                project_key="project-key",
                stage="verify",
                completed_batches=["café", "東京", "नमस्ते", "🚀"],
                remaining_batches=["José", "München"],
                intent="preserve nested unicode metadata: {'user': {'name': 'José'}, 'tags': ['café', '東京']}",
            )
            store.save(checkpoint)
            restored = SessionStore(root).load("unicode-session")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.completed_batches, checkpoint.completed_batches)
            self.assertEqual(restored.remaining_batches, checkpoint.remaining_batches)
            self.assertEqual(restored.intent, checkpoint.intent)
            self.assertEqual(restored.state_digest, checkpoint.state_digest)



class StateMigrationPreflightCliTests(unittest.TestCase):
    def _run_cli(self, canonical: Path, legacy: Path):
        repo_root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [
                sys.executable, "-m", "scripts.plan_state_migration",
                "--canonical-root", str(canonical),
                "--legacy-root", str(legacy),
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    def test_cli_emits_read_only_plan_and_does_not_create_canonical_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "sessions" / "session-cli.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"session_id":"cli"}\\n', encoding="utf-8")

            result = self._run_cli(canonical, legacy)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertTrue(plan["safe_to_apply"])
            self.assertEqual(plan["items"][0]["action"], "copy")
            self.assertFalse(canonical.exists())
            self.assertTrue(source.is_file())

    def test_cli_returns_two_for_conflict_without_mutating_either_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, canonical = base / ".aer", base / ".auren"
            source = legacy / "sessions" / "session-cli.json"
            target = canonical / "sessions" / "session-cli.json"
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_text('{"session_id":"legacy"}\\n', encoding="utf-8")
            target.write_text('{"session_id":"canonical"}\\n', encoding="utf-8")

            result = self._run_cli(canonical, legacy)

            self.assertEqual(result.returncode, 2, result.stderr)
            plan = json.loads(result.stdout)
            self.assertFalse(plan["safe_to_apply"])
            self.assertEqual(plan["conflicts"], ["sessions/session-cli.json"])
            self.assertEqual(source.read_text(encoding="utf-8"), '{"session_id":"legacy"}\\n')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"session_id":"canonical"}\\n')


if __name__ == "__main__":
    unittest.main()
